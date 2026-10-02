from typing import Any, Dict, List

import numpy as np
import soundfile as sf

from app.core.logging import get_logger

logger = get_logger("chunking")


def find_best_silence_split(
    audio: np.ndarray,
    sr: int,
    min_split_sample: int,
    max_split_sample: int,
    min_silence_sec: float = 0.3,
) -> int:
    """
    Finds the optimal cut point within [min_split_sample, max_split_sample].
    Prefers Silero VAD non-speech gaps. Falls back to minimal RMS short-time energy.
    """
    min_split_sample = max(0, min_split_sample)
    max_split_sample = min(len(audio), max_split_sample)

    if max_split_sample <= min_split_sample:
        return (min_split_sample + max_split_sample) // 2

    # 1. Try Silero VAD speech timestamps
    try:
        from faster_whisper.vad import get_speech_timestamps

        speech_intervals = get_speech_timestamps(audio, sampling_rate=sr)
    except Exception as e:
        logger.debug("vad_chunk_timestamp_fallback", error=str(e))
        speech_intervals = []

    if speech_intervals:
        min_silence_samples = int(min_silence_sec * sr)
        candidate_splits = []

        # Find non-speech gaps
        # Gap before first speech
        if speech_intervals[0]["start"] >= min_silence_samples:
            gap_start, gap_end = 0, speech_intervals[0]["start"]
            mid = (gap_start + gap_end) // 2
            if min_split_sample <= mid <= max_split_sample:
                candidate_splits.append((gap_end - gap_start, mid))

        # Gaps between speech blocks
        for i in range(len(speech_intervals) - 1):
            gap_start = speech_intervals[i]["end"]
            gap_end = speech_intervals[i + 1]["start"]
            if gap_end - gap_start >= min_silence_samples:
                mid = (gap_start + gap_end) // 2
                if min_split_sample <= mid <= max_split_sample:
                    candidate_splits.append((gap_end - gap_start, mid))

        # Gap after last speech
        if len(audio) - speech_intervals[-1]["end"] >= min_silence_samples:
            gap_start = speech_intervals[-1]["end"]
            gap_end = len(audio)
            mid = (gap_start + gap_end) // 2
            if min_split_sample <= mid <= max_split_sample:
                candidate_splits.append((gap_end - gap_start, mid))

        if candidate_splits:
            # Pick the largest silence gap within the search range
            candidate_splits.sort(key=lambda x: x[0], reverse=True)
            return candidate_splits[0][1]

    # 2. Fallback: Short-time RMS energy minimization
    frame_len = int(0.05 * sr)  # 50 ms
    hop_len = int(0.025 * sr)  # 25 ms
    search_audio = audio[min_split_sample:max_split_sample]

    if len(search_audio) < frame_len:
        return (min_split_sample + max_split_sample) // 2

    num_frames = (len(search_audio) - frame_len) // hop_len + 1
    energies = [
        float(np.sqrt(np.mean(search_audio[i * hop_len : i * hop_len + frame_len] ** 2)))
        for i in range(num_frames)
    ]
    min_idx = int(np.argmin(energies))
    best_sample = min_split_sample + min_idx * hop_len + frame_len // 2
    return best_sample


def chunk_audio_adaptive(
    audio_path: str,
    target_chunk_sec: int = 300,
    min_chunk_sec: int = 180,
    max_chunk_sec: int = 420,
    min_silence_sec: float = 0.3,
) -> List[Dict[str, Any]]:
    """
    Adaptive VAD-guided audio chunking.
    Finds natural silence boundaries to split audio without truncating words.
    """
    info = sf.info(audio_path)
    sr = info.samplerate
    total_frames = info.frames
    total_sec = total_frames / sr

    if total_sec <= max_chunk_sec:
        data, _ = sf.read(audio_path, dtype="float32")
        if data.ndim > 1:
            data = np.mean(data, axis=1)
        return [
            {
                "data": data,
                "start_sec": 0.0,
                "end_sec": total_sec,
                "overlap_sec": 0.0,
                "is_silence_split": True,
            }
        ]

    audio, _ = sf.read(audio_path, dtype="float32")
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)

    chunks = []
    current_start = 0

    while (total_frames - current_start) > max_chunk_sec * sr:
        min_split = current_start + int(min_chunk_sec * sr)
        max_split = current_start + int(max_chunk_sec * sr)

        split_sample = find_best_silence_split(
            audio, sr, min_split, max_split, min_silence_sec=min_silence_sec
        )

        chunk_data = audio[current_start:split_sample]
        chunks.append(
            {
                "data": chunk_data,
                "start_sec": current_start / sr,
                "end_sec": split_sample / sr,
                "overlap_sec": 0.0,
                "is_silence_split": True,
            }
        )
        current_start = split_sample

    # Trailing chunk
    if current_start < total_frames:
        chunk_data = audio[current_start:total_frames]
        chunks.append(
            {
                "data": chunk_data,
                "start_sec": current_start / sr,
                "end_sec": total_frames / sr,
                "overlap_sec": 0.0,
                "is_silence_split": True,
            }
        )

    logger.info(
        "adaptive_vad_chunking_complete",
        total_chunks=len(chunks),
        total_duration=round(total_sec, 2),
    )
    return chunks


def chunk_audio(
    audio_path: str,
    chunk_length_sec: int = 600,
    overlap_sec: int = 15,
    use_adaptive_vad: bool = True,
) -> List[Dict[str, Any]]:
    """
    Main chunking entry point.
    Uses adaptive VAD chunking by default, with fallback to legacy fixed-step chunking.
    """
    if use_adaptive_vad:
        target_sec = max(60, chunk_length_sec // 2)
        min_sec = max(30, int(chunk_length_sec * 0.35))
        max_sec = chunk_length_sec
        return chunk_audio_adaptive(
            audio_path,
            target_chunk_sec=target_sec,
            min_chunk_sec=min_sec,
            max_chunk_sec=max_sec,
        )

    info = sf.info(audio_path)
    sr = info.samplerate
    total_frames = info.frames

    chunk_frames = chunk_length_sec * sr
    overlap_frames = overlap_sec * sr
    step_frames = chunk_frames - overlap_frames

    chunks = []
    start_frame = 0
    while start_frame < total_frames:
        frames_to_read = min(chunk_frames, total_frames - start_frame)
        data, _ = sf.read(audio_path, frames=frames_to_read, start=start_frame, dtype="float32")
        chunks.append(
            {
                "data": data,
                "start_sec": start_frame / sr,
                "end_sec": (start_frame + frames_to_read) / sr,
                "overlap_sec": overlap_sec
                if (start_frame + frames_to_read) < total_frames
                else 0.0,
                "is_silence_split": False,
            }
        )
        if start_frame + chunk_frames >= total_frames:
            break
        start_frame += step_frames

    return chunks


def merge_and_deduplicate(
    all_segments: List[List[Dict[str, Any]]],
    chunks_info: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Merges segments from multiple chunks, correcting timestamps.
    For silence-split chunks, concatenation is lossless without drops or overlaps.
    For overlapping chunks, deduplicates segments across the overlap boundaries.
    """
    merged = []

    # If all chunks are cleanly silence-split with zero overlap
    is_clean_silence_split = all(c.get("overlap_sec", 0.0) == 0.0 for c in chunks_info)

    if is_clean_silence_split:
        for chunk_segs, info in zip(all_segments, chunks_info):
            chunk_offset = info["start_sec"]
            for s in chunk_segs:
                s_copy = dict(s)
                s_copy["start"] += chunk_offset
                s_copy["end"] += chunk_offset
                if "words" in s_copy and s_copy["words"]:
                    adjusted_words = []
                    for w in s_copy["words"]:
                        w_copy = dict(w)
                        w_copy["start"] += chunk_offset
                        w_copy["end"] += chunk_offset
                        adjusted_words.append(w_copy)
                    s_copy["words"] = adjusted_words
                merged.append(s_copy)
        return merged

    # Fallback: legacy overlap deduplication
    for i, (chunk_segs, info) in enumerate(zip(all_segments, chunks_info)):
        chunk_offset = info["start_sec"]

        take_start = info["start_sec"]
        if i > 0:
            take_start = info["start_sec"] + (chunks_info[i - 1]["overlap_sec"] / 2.0)

        take_end = info["end_sec"]
        if i < len(all_segments) - 1:
            take_end = info["end_sec"] - (info["overlap_sec"] / 2.0)

        for s in chunk_segs:
            s_copy = dict(s)
            s_copy["start"] += chunk_offset
            s_copy["end"] += chunk_offset

            mid_point = (s_copy["start"] + s_copy["end"]) / 2.0

            if take_start <= mid_point < take_end:
                if "words" in s_copy and s_copy["words"]:
                    adjusted_words = []
                    for w in s_copy["words"]:
                        w_copy = dict(w)
                        w_copy["start"] += chunk_offset
                        w_copy["end"] += chunk_offset
                        adjusted_words.append(w_copy)
                    s_copy["words"] = adjusted_words
                merged.append(s_copy)

    return merged
