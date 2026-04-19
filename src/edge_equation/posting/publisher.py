from .formatter import PostPayload

class Publisher:
    """Placeholder publisher."""
    def publish(self, payload: PostPayload) -> None:
        print(payload.text)
