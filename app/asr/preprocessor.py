import json
import os
import subprocess

try:
    import resource
except ImportError:
    resource = None

import numpy as np

from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger("audio_preprocessor")


def set_process_limits():
    """Restricts CPU time and address space for FFmpeg subprocesses."""
    if resource is None:
        return
    try:
        # Max 300 seconds CPU time
        resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
        # Max 1.5 GB virtual memory
        resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024, 1536 * 1024 * 1024))
    except Exception:
        pass


class AudioPreprocessor:
    def __init__(self):
        pass

    def probe_audio(self, file_path: str) -> dict[str, any]:
        """Runs ffprobe securely to extract format, duration and channels."""
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,bit_rate:stream=codec_name,channels,sample_rate",
            "-of",
            "json",
            file_path,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
                # preexec_fn — только POSIX; на Windows этот параметр
                # не поддерживается и роняет любой вызов (см. B8-тесты).
                preexec_fn=set_process_limits if os.name == "posix" else None,
            )
            data = json.loads(result.stdout)
            format_info = data.get("format", {})
            streams = data.get("streams", [])
            audio_stream = streams[0] if streams else {}

            duration = float(format_info.get("duration", 0.0))
            channels = int(audio_stream.get("channels", 1))
            sample_rate = int(audio_stream.get("sample_rate", 16000))
            codec = audio_stream.get("codec_name", "unknown")

            return {
                "duration": duration,
                "channels": channels,
                "sample_rate": sample_rate,
                "codec": codec,
            }
        except subprocess.TimeoutExpired:
            raise AppError("probe_timeout", "Превышено время анализа аудиофайла", 400)
        except Exception as e:
            logger.error("ffprobe_error", error=str(e), file_path=file_path)
            raise AppError(
                "invalid_audio", "Не удалось прочитать аудиофайл или повреждены метаданные", 400
            )

    def preprocess_memory(self, audio_bytes: bytes) -> bytes:
        """
        Fast in-memory audio cleaning for real-time voice search:
        - High-pass filter (80 Hz) to eliminate microphone DC offset and sub-rumble
        - Loudnorm to normalize speech volume to -16 LUFS
        - Resamples to 16 kHz 16-bit mono PCM WAV
        """
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-threads",
            "1",
            "-i",
            "pipe:0",
            "-vn",
            "-af",
            "highpass=f=80,lowpass=f=7600,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "wav",
            "pipe:1",
        ]
        try:
            res = subprocess.run(
                cmd,
                input=audio_bytes,
                capture_output=True,
                check=True,
                timeout=10,
                preexec_fn=set_process_limits if os.name == "posix" else None,
            )
            return res.stdout
        except Exception as e:
            logger.warning("memory_audio_preprocess_fallback", error=str(e))
            return audio_bytes

    def convert_to_pcm16(
        self,
        input_path: str,
        output_path: str,
        target_sample_rate: int = 16000,
        channels: int = 1,
    ) -> str:
        """Converts any audio file to normalized 16kHz 16-bit PCM WAV via FFmpeg."""
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-threads",
            "1",
            "-i",
            input_path,
            "-vn",
            "-af",
            "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar",
            str(target_sample_rate),
            "-ac",
            str(channels),
            "-f",
            "wav",
            output_path,
        ]

        try:
            subprocess.run(
                cmd,
                capture_output=True,
                check=True,
                timeout=300,
                preexec_fn=set_process_limits if os.name == "posix" else None,
            )
            return output_path
        except subprocess.TimeoutExpired:
            raise AppError("conversion_timeout", "Превышено время конвертации аудио", 500)
        except subprocess.CalledProcessError as e:
            logger.error("ffmpeg_convert_error", error=e.stderr.decode("utf-8", errors="ignore"))
            raise AppError("conversion_failed", "Ошибка при декодировании аудиофайла", 500)

    def split_stereo_channels(self, input_path: str) -> tuple[str, str]:
        """Splits a stereo file into two separate mono WAV files."""
        dir_name = os.path.dirname(input_path)
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        ch0_path = os.path.join(dir_name, f"{base_name}_ch0.wav")
        ch1_path = os.path.join(dir_name, f"{base_name}_ch1.wav")

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-threads",
            "1",
            "-i",
            input_path,
            "-filter_complex",
            "[0:a]channelsplit=channel_layout=stereo[left][right]",
            "-map",
            "[left]",
            ch0_path,
            "-map",
            "[right]",
            ch1_path,
        ]

        try:
            subprocess.run(
                cmd,
                capture_output=True,
                check=True,
                timeout=180,
                preexec_fn=set_process_limits if os.name == "posix" else None,
            )
            return ch0_path, ch1_path
        except Exception as e:
            logger.warning("ffmpeg_stereo_split_failed_trying_fallback", error=str(e))
            try:
                import soundfile as sf

                data, sr = sf.read(input_path)
                if data.ndim >= 2:
                    sf.write(ch0_path, data[:, 0], sr)
                    sf.write(ch1_path, data[:, 1], sr)
                    return ch0_path, ch1_path
            except Exception as fe:
                logger.error("stereo_split_fallback_error", error=str(fe))
            raise AppError("stereo_split_failed", "Ошибка разделения стереоканалов", 500)

    def check_channel_isolation(self, input_path: str, max_check_sec: float = 60.0) -> bool:
        """
        Determines whether a multi-channel audio file represents true isolated stereo
        (e.g., dedicated telephony channels where channel 0 is manager and channel 1 is client),
        or shared/dual-mono/acoustic bleed where both channels capture the same speech.

        Returns True if channels are acoustically isolated (<30% energy overlap and <0.35 correlation),
        False if channels share common audio (dual-mono, stereo microphone bleed, podcast).
        """
        try:
            import soundfile as sf

            with sf.SoundFile(input_path) as sfile:
                if sfile.channels < 2:
                    return False
                sr = sfile.samplerate
                frames_to_read = min(len(sfile), int(sr * max_check_sec))
                data = sfile.read(frames_to_read, dtype="float32")

            ch0 = data[:, 0]
            ch1 = data[:, 1]

            # Compute short-term energy envelopes (50ms window, 25ms hop)
            frame_sz = max(1, int(sr * 0.05))
            hop = max(1, int(sr * 0.025))

            if len(ch0) <= frame_sz:
                return False

            e0 = np.array(
                [
                    float(np.sqrt(np.mean(ch0[i : i + frame_sz] ** 2)))
                    for i in range(0, len(ch0) - frame_sz, hop)
                ],
                dtype=np.float32,
            )
            e1 = np.array(
                [
                    float(np.sqrt(np.mean(ch1[i : i + frame_sz] ** 2)))
                    for i in range(0, len(ch1) - frame_sz, hop)
                ],
                dtype=np.float32,
            )

            th0 = float(np.percentile(e0, 30)) + 1e-5
            th1 = float(np.percentile(e1, 30)) + 1e-5

            act0 = e0 > th0
            act1 = e1 > th1

            active_union = np.sum(act0 | act1)
            if active_union == 0:
                return False

            overlap = float(np.sum(act0 & act1) / active_union)
            corr_mat = np.corrcoef(e0, e1)
            corr = float(corr_mat[0, 1]) if not np.isnan(corr_mat[0, 1]) else 0.0

            logger.info(
                "channel_isolation_metrics",
                overlap=round(overlap, 3),
                correlation=round(corr, 3),
                file_path=input_path,
            )

            # In true telephony stereo, speakers alternate turns (low correlation < 0.35)
            # and simultaneous speech is minimal (< 0.30 overlap).
            return bool(overlap < 0.30 and corr < 0.35)
        except Exception as e:
            logger.warning("channel_isolation_check_error", error=str(e), file_path=input_path)
            return False
