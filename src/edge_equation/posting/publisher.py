from .formatter import PostPayload


class Publisher:
    """Placeholder publisher."""

    def publish(self, payload: PostPayload) -> None:
        # TODO: integrate with X API later
        print(payload.text)
