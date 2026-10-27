"""Observe full skill output, including MCP result text; never call arguments.

Literal decoding is deliberately non-executing. Unsupported output is left
unproven and can only be adjudicated against the same full-body criterion.
"""
import ast
import json
import re

LITERAL = r'''(?:'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")'''
EXPRESSION = re.compile(LITERAL + r'(?:\s*\+\s*' + LITERAL + r')*')


def strings(value, depth=0):
    if depth > 8:
        return
    if isinstance(value, str):
        yield value
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            decoded = None
        if isinstance(decoded, (dict, list)):
            yield from strings(decoded, depth + 1)
        for match in EXPRESSION.finditer(value):
            try:
                yield ''.join(ast.literal_eval(token) for token in re.findall(LITERAL, match.group()))
            except (ValueError, SyntaxError):
                continue
    elif isinstance(value, list):
        for item in value:
            yield from strings(item, depth + 1)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item, depth + 1)


def body(text):
    return re.sub(r'\A---\n.*?\n---\n', '', text.replace('\r\n', '\n'), count=1, flags=re.S).strip()


def observe(raw, skill):
    expected = body(skill)
    proof, threads, errors = [], [], []
    for number, line in enumerate(raw.splitlines(), 1):
        try:
            event = json.loads(line)
        except ValueError:
            errors.append(number)
            continue
        if event.get('type') == 'thread.started':
            threads.append(event.get('thread_id'))
        if event.get('type') != 'item.completed':
            continue
        item = event.get('item', {})
        kind = item.get('type')
        if kind == 'command_execution':
            outputs = [item.get('aggregated_output', '')]
        elif kind == 'mcp_tool_call':
            outputs = strings(item.get('result'))
        else:
            continue
        if expected and any(expected in output.replace('\r\n', '\n') for output in outputs):
            proof.append({'event_line': number, 'item_id': item.get('id'), 'type': kind})
    return {'body_visible': bool(proof), 'proof': proof, 'thread_ids': threads,
            'malformed_lines': errors, 'method': 'completed-tool-result-full-body-v1'}
