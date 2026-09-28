import json
import os
import resource
import subprocess

from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger("audio_preprocessor")


def set_process_limits():
    """Restricts CPU time and address space for FFmpeg subprocesses."""
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
            "-v", "error",
            "-show_entries", "format=duration,size,bit_rate:stream=codec_name,channels,sample_rate",
            "-of", "json",
            file_path,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
                preexec_fn=set_process_limits,
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
            raise AppError("invalid_audio", "Не удалось прочитать аудиофайл или повреждены метаданные", 400)

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
            "-threads", "1",
            "-i", input_path,
            "-vn",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar", str(target_sample_rate),
            "-ac", str(channels),
            "-f", "wav",
            output_path,
        ]

        try:
            subprocess.run(
                cmd,
                capture_output=True,
                check=True,
                timeout=300,
                preexec_fn=set_process_limits,
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
            "-threads", "1",
            "-i", input_path,
            "-map_channel", "0.0.0", ch0_path,
            "-map_channel", "0.0.1", ch1_path,
        ]

        try:
            subprocess.run(
                cmd,
                capture_output=True,
                check=True,
                timeout=180,
                preexec_fn=set_process_limits,
            )
            return ch0_path, ch1_path
        except Exception as e:
            logger.error("stereo_split_error", error=str(e))
            raise AppError("stereo_split_failed", "Ошибка разделения стереоканалов", 500)
