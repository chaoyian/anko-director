from anko_director.discord_service import split_discord_message


def test_discord_split_preserves_content() -> None:
    content = ("第一段。" * 300) + "\n" + ("第二段。" * 300)
    chunks = split_discord_message(content, limit=500)
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == content.replace("\n", "")
