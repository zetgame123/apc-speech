import soundfile as sf
import numpy as np
import os
from typing import List, Dict, Any

def chunk_audio(audio_path: str, chunk_length_sec: int = 600, overlap_sec: int = 15) -> List[Dict[str, Any]]:
    """Reads audio file in overlapping chunks."""
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
        data, _ = sf.read(audio_path, frames=frames_to_read, start=start_frame, dtype='float32')
        chunks.append({
            "data": data,
            "start_sec": start_frame / sr,
            "end_sec": (start_frame + frames_to_read) / sr,
            "overlap_sec": overlap_sec if (start_frame + frames_to_read) < total_frames else 0
        })
        if start_frame + chunk_frames >= total_frames:
            break
        start_frame += step_frames
        
    return chunks

def merge_and_deduplicate(all_segments: List[List[Dict[str, Any]]], chunks_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merges segments from multiple chunks, correcting timestamps and deduplicating overlaps."""
    merged = []
    
    for i, (chunk_segs, info) in enumerate(zip(all_segments, chunks_info)):
        chunk_offset = info["start_sec"]
        
        # Calculate boundaries for taking segments from this chunk
        # If it's the first chunk, take everything up to the middle of the overlap with the NEXT chunk.
        # If it's a middle chunk, take everything from the middle of the overlap with PREV chunk to middle of overlap with NEXT chunk.
        
        take_start = info["start_sec"]
        if i > 0:
            take_start = info["start_sec"] + (chunks_info[i-1]["overlap_sec"] / 2.0)
            
        take_end = info["end_sec"]
        if i < len(all_segments) - 1:
            take_end = info["end_sec"] - (info["overlap_sec"] / 2.0)
        
        for s in chunk_segs:
            s_copy = dict(s)
            s_copy["start"] += chunk_offset
            s_copy["end"] += chunk_offset
            
            mid_point = (s_copy["start"] + s_copy["end"]) / 2.0
            
            if mid_point >= take_start and mid_point < take_end:
                if "words" in s_copy and s_copy["words"]:
                    for w in s_copy["words"]:
                        w["start"] += chunk_offset
                        w["end"] += chunk_offset
                merged.append(s_copy)
            
    return merged
