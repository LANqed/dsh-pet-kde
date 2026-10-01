# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from pet.dsh_events import normalize_event
from pet.music import Track, lyric_at, parse_lrc


def test_parse_lrc_and_lookup():
    lines = parse_lrc('[00:01.20]第一句\n[00:03.50]第二句\n[bad]忽略')
    assert lines == [(1.2, '第一句'), (3.5, '第二句')]
    assert lyric_at(lines, 0.0) == ''
    assert lyric_at(lines, 2.0) == '第一句'
    assert lyric_at(lines, 4.0) == '第二句'


def test_track_query_and_playing():
    track = Track(title='歌', artist='歌手', status='Playing')
    assert track.query == '歌手 - 歌'
    assert track.is_playing is True


def test_dsh_event_normalization():
    assert normalize_event({'event': 'turn/start', 'message': '思考'}) == ('thinking', '思考')
    assert normalize_event({'event': 'tool/call', 'tool': 'bash'}) == ('working', 'bash')
    assert normalize_event({'event': 'approval/asked'})[0] == 'attention'
    assert normalize_event({'event': 'turn/end', 'status': 'completed'})[0] == 'idle'
    assert normalize_event({'event': 'turn/end', 'status': 'error'})[0] == 'error'
    assert normalize_event({'event': 'unknown'}) == (None, '')
