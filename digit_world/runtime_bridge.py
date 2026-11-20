class RuntimeBridge:

    def move(
        self,
        player_id,
        x,
        y,
        dx,
        dy,
    ):
        return {
            "approved": True,
            "new_x": x + dx,
            "new_y": y + dy,
            "source": "runtime-bridge-v0",
        }
