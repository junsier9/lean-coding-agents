import os
from pathlib import Path
from errors import AssetMissing, Opener

def download(opener: Opener, target, replace=os.replace):
    """Download into a caller-selected destination.

    The caller owns the destination path; we own an opened response.
    """
    try:
        response = opener()
    except AssetMissing:
        return False
    target = Path(target)
    with target.open("wb") as output:
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            output.write(chunk)
    response.close()
    return True
