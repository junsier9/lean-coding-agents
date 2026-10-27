import os
from pathlib import Path
import tempfile
from errors import AssetMissing, Opener

def download(opener: Opener, target, replace=os.replace):
    try:
        response = opener()
    except AssetMissing:
        return False
    temporary = None
    target = Path(target)
    try:
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    output.write(chunk)
        except BaseException:
            try:
                response.close()
            except Exception:
                pass
            raise
        response.close()
        replace(temporary, target)
        return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
