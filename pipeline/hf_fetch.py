"""
Hugging Face Audio Fetcher
===========================
Fetches audio files from the dataset repository `Pandago/qad-buc`
on-the-fly and makes them available for composition.
"""

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

try:
    from huggingface_hub import hf_hub_download
except ImportError:
    raise ImportError("huggingface_hub is required. Please install it using 'pip install huggingface_hub'")


@contextmanager
def fetch_audio_batch(all_source_paths: list[str]):
    """
    Context manager that downloads a batch of audio files from Hugging Face
    and maps their relative source paths to temporary local file paths.
    Cleans up the temporary files automatically upon exit.
    """
    token = os.environ.get("HF_API_KEY")
    repo_id = "Pandago/qad-buc"
    
    # Create a unique temporary directory
    temp_dir = tempfile.mkdtemp()
    local_map = {}
    
    print(f"\n[hf_fetch] Preparing to fetch {len(all_source_paths)} audio track(s) from dataset '{repo_id}'...")
    
    try:
        for source_path in all_source_paths:
            if not source_path or source_path in local_map:
                continue
            
            # Normalize path slashes
            filename = source_path.replace("\\", "/")
            
            print(f"[hf_fetch] Fetching: {filename}")
            try:
                # Download file to local HF cache
                cached_path = hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    repo_type="dataset",
                    token=token,
                )
                
                # Copy from cache to temp directory with flat filename to avoid nested dir structures
                temp_file_name = f"audio_{len(local_map)}_{Path(filename).name}"
                dest_path = Path(temp_dir) / temp_file_name
                
                shutil.copy2(cached_path, dest_path)
                local_map[source_path] = str(dest_path)
                
            except Exception as e:
                print(f"[hf_fetch] ERROR: Failed to download '{filename}': {e}")
                
        yield local_map
        
    finally:
        # Cleanup temp directory and all copied files
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("[hf_fetch] Temporary audio files cleaned up.")
