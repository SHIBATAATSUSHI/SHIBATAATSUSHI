"""note_api のうち、ネットワークに触れない部分(アカウント解決)のテスト。"""

from __future__ import annotations

import json

import pytest

from note_api import Account, AuthError, NoteError, load_accounts, resolve_account

# 存在しないパスを渡して、ローカル設定ファイルの影響を受けないようにする
NO_FILE = "/nonexistent/.note-accounts.json"


def env(**kwargs: str) -> dict[str, str]:
    """テスト用の環境変数辞書を作る。"""
    return dict(kwargs)


# --------------------------------------------------------------------------
# 環境変数からの読み込み
# --------------------------------------------------------------------------

def test_環境変数からアカウントを読む():
    accounts = load_accounts(
        env=env(NOTE_TORA_URLNAME="akutagawa_tora", NOTE_TORA_SESSION="abc123"),
        config_path=NO_FILE,
    )
    assert list(accounts) == ["tora"]
    assert accounts["tora"].urlname == "akutagawa_tora"
    assert accounts["tora"].source == "環境変数"


def test_複数アカウントを並べて読める():
    accounts = load_accounts(
        env=env(
            NOTE_TORA_URLNAME="akutagawa_tora",
            NOTE_TORA_SESSION="aaa",
            NOTE_SUB_URLNAME="another_one",
            NOTE_SUB_SESSION="bbb",
        ),
        config_path=NO_FILE,
    )
    assert sorted(accounts) == ["sub", "tora"]


def test_URLNAMEが無いアカウントは無視される():
    accounts = load_accounts(env=env(NOTE_TORA_SESSION="aaa"), config_path=NO_FILE)
    assert accounts == {}


def test_SESSIONが空文字なら無視される():
    accounts = load_accounts(
        env=env(NOTE_TORA_URLNAME="akutagawa_tora", NOTE_TORA_SESSION="   "),
        config_path=NO_FILE,
    )
    assert accounts == {}


def test_NOTE_DEFAULT_ACCOUNTはアカウントとして誤認しない():
    # NOTE_DEFAULT_ACCOUNT は制御用の変数であってアカウント定義ではない
    accounts = load_accounts(
        env=env(
            NOTE_DEFAULT_ACCOUNT="tora",
            NOTE_TORA_URLNAME="akutagawa_tora",
            NOTE_TORA_SESSION="aaa",
        ),
        config_path=NO_FILE,
    )
    assert list(accounts) == ["tora"]


# --------------------------------------------------------------------------
# 設定ファイルからの読み込み
# --------------------------------------------------------------------------

def test_設定ファイルから読む(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(
        json.dumps({"tora": {"urlname": "akutagawa_tora", "session": "aaa"}}), encoding="utf-8"
    )
    accounts = load_accounts(env={}, config_path=str(path))
    assert accounts["tora"].urlname == "akutagawa_tora"
    assert accounts["tora"].source == str(path)


def test_同名なら環境変数が設定ファイルより優先される(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(
        json.dumps({"tora": {"urlname": "from_file", "session": "aaa"}}), encoding="utf-8"
    )
    accounts = load_accounts(
        env=env(NOTE_TORA_URLNAME="from_env", NOTE_TORA_SESSION="bbb"),
        config_path=str(path),
    )
    assert accounts["tora"].urlname == "from_env"


def test_壊れた設定ファイルはエラーになる(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text("{ これは JSON ではない", encoding="utf-8")
    with pytest.raises(NoteError, match="読めませんでした"):
        load_accounts(env={}, config_path=str(path))


# --------------------------------------------------------------------------
# アカウントの決定
# --------------------------------------------------------------------------

def test_明示指定が最優先():
    e = env(
        NOTE_DEFAULT_ACCOUNT="sub",
        NOTE_TORA_URLNAME="akutagawa_tora",
        NOTE_TORA_SESSION="aaa",
        NOTE_SUB_URLNAME="another_one",
        NOTE_SUB_SESSION="bbb",
    )
    assert resolve_account("tora", env=e, config_path=NO_FILE).urlname == "akutagawa_tora"


def test_指定が無ければNOTE_DEFAULT_ACCOUNTを使う():
    e = env(
        NOTE_DEFAULT_ACCOUNT="sub",
        NOTE_TORA_URLNAME="akutagawa_tora",
        NOTE_TORA_SESSION="aaa",
        NOTE_SUB_URLNAME="another_one",
        NOTE_SUB_SESSION="bbb",
    )
    assert resolve_account(None, env=e, config_path=NO_FILE).name == "sub"


def test_1件だけなら指定なしでもそれを使う():
    e = env(NOTE_TORA_URLNAME="akutagawa_tora", NOTE_TORA_SESSION="aaa")
    assert resolve_account(None, env=e, config_path=NO_FILE).name == "tora"


def test_複数あって指定が無ければエラー():
    e = env(
        NOTE_TORA_URLNAME="akutagawa_tora",
        NOTE_TORA_SESSION="aaa",
        NOTE_SUB_URLNAME="another_one",
        NOTE_SUB_SESSION="bbb",
    )
    with pytest.raises(AuthError, match="--account"):
        resolve_account(None, env=e, config_path=NO_FILE)


def test_大文字小文字を無視して指定できる():
    e = env(NOTE_TORA_URLNAME="akutagawa_tora", NOTE_TORA_SESSION="aaa")
    assert resolve_account("TORA", env=e, config_path=NO_FILE).name == "tora"


def test_存在しないアカウント名はエラーで候補を示す():
    e = env(NOTE_TORA_URLNAME="akutagawa_tora", NOTE_TORA_SESSION="aaa")
    with pytest.raises(AuthError, match="tora"):
        resolve_account("nonexistent", env=e, config_path=NO_FILE)


def test_設定ゼロなら取得手順を案内する():
    with pytest.raises(AuthError, match="_note_session_v5"):
        resolve_account(None, env={}, config_path=NO_FILE)


def test_DEFAULT_ACCOUNTの指す先が無ければエラー():
    e = env(
        NOTE_DEFAULT_ACCOUNT="missing",
        NOTE_TORA_URLNAME="akutagawa_tora",
        NOTE_TORA_SESSION="aaa",
    )
    with pytest.raises(AuthError, match="NOTE_DEFAULT_ACCOUNT"):
        resolve_account(None, env=e, config_path=NO_FILE)


# --------------------------------------------------------------------------
# Cookie のマスク
# --------------------------------------------------------------------------

def test_Cookieは伏せて表示される():
    a = Account(name="tora", urlname="akutagawa_tora", session="s" * 40, source="環境変数")
    masked = a.masked_session
    assert a.session not in masked
    assert masked.startswith("ssssss")
    assert "40文字" in masked


def test_短いCookieは全部伏せる():
    a = Account(name="tora", urlname="akutagawa_tora", session="abc", source="環境変数")
    assert a.masked_session == "***"
