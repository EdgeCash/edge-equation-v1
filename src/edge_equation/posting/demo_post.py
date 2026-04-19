from .formatter import PostPayload


def build_demo_post(result: dict) -> PostPayload:
    text = f"Engine ran successfully. Result: {result}"
    return PostPayload(text=text)
