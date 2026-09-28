import soundfile as sf
import numpy as np
import os
from typing import List, Dict, Any

def chunk_audio(audio_path: str, chunk_length_sec: int = 600, overlap_sec: int = 15) -> List[np.ndarray]:
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
        
        for s in chunk_segs:
            s_copy = dict(s)
            s_copy["start"] += chunk_offset
            s_copy["end"] += chunk_offset
            if "words" in s_copy and s_copy["words"]:
                for w in s_copy["words"]:
                    w["start"] += chunk_offset
                    w["end"] += chunk_offset
            
            # Deduplication: if this segment is strictly within the overlap region of the previous chunk
            # and we already added a very similar segment, skip it.
            # A simpler heuristic: only take segments whose mid-point is before the end of the step 
            # (i.e. start_sec + chunk_length_sec - overlap_sec) UNLESS it's the last chunk.
            
            mid_point = (s_copy["start"] + s_copy["end"]) / 2.0
            
            if i < len(all_segments) - 1:
                # not the last chunk
                chunk_step_end = info["end_sec"] - (info["end_sec"] - info["start_sec"] - (info["end_sec"] - info["start_sec"])) # wait
                # overlap_sec is given, but info only has start_sec and end_sec
                # Actually, the cutoff is the start of the next chunk + overlap_sec / 2
                next_chunk_start = chunks_info[i+1]["start_sec"]
                cutoff = next_chunk_start + 7.5 # half of 15s overlap
                if mid_point > cutoff:
                    continue # Skip this segment, let the next chunk handle it
                    
            if i > 0:
                # For chunks > 0, we skipped the end of the previous chunk, but maybe the start of this chunk 
                # overlaps with the end of the previous chunk.
                # The cutoff was next_chunk_start + 7.5. We should only take segments after this cutoff.
                prev_cutoff = info["start_sec"] + 7.5
                if mid_point <= prev_cutoff:
                    continue # Handled by previous chunk
                    
            merged.append(s_copy)
            
    return merged
