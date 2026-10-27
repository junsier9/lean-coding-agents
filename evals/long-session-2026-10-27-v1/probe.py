"""One bounded local operation per native-model round trip; no network."""
from pathlib import Path
import argparse
import json
import secrets
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--step', type=int, required=True)
    parser.add_argument('--previous', required=True)
    args = parser.parse_args()
    state_path = Path('probe-state.json')
    state = json.loads(state_path.read_text(encoding='utf-8'))
    if args.step != state['step'] + 1 or args.previous != state['token'] or not 1 <= args.step <= 12:
        raise ValueError('Call exactly the next step with the token from its preceding tool output')
    start = time.monotonic()
    time.sleep(25)
    token = secrets.token_hex(8)
    result = {'native_session_probe': True, 'step': args.step,
              'previous': args.previous, 'next_token': token,
              'tool_seconds': time.monotonic() - start}
    Path(f'round-{args.step:02}.json').write_text(json.dumps(result), encoding='utf-8')
    state_path.write_text(json.dumps({'step': args.step, 'token': token}), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
