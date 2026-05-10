import numpy as np
import rerun as rr
from dora import Node

# DaBai DC1 depth intrinsics (from get_camera_param())
DEPTH_FX = 478.42
DEPTH_FY = 478.42
DEPTH_CX = 324.14
DEPTH_CY = 196.82


def main():
    rr.init("orbbec-viewer", spawn=True)

    rr.log(
        "camera/depth",
        rr.Pinhole(
            focal_length=(DEPTH_FX, DEPTH_FY),
            principal_point=(DEPTH_CX, DEPTH_CY),
            width=640,
            height=400,
        ),
        static=True,
    )

    node = Node()

    for event in node:
        if event["type"] != "INPUT":
            continue

        event_id = event["id"]
        data = event["value"]

        if event_id == "image":
            img_bytes = np.asarray(data, dtype=np.uint8).tobytes()
            rr.log(
                "camera/color",
                rr.EncodedImage(contents=img_bytes, media_type="image/jpeg"),
            )

        elif event_id == "depth":
            meta = event.get("metadata", {})
            width = int(meta.get("width", 640))
            height = int(meta.get("height", 400))
            depth = np.asarray(data, dtype=np.float32).reshape(height, width)
            rr.log("camera/depth/image", rr.DepthImage(depth, meter=1.0))

        elif event_id == "image_depth":
            img_bytes = np.asarray(data, dtype=np.uint8).tobytes()
            rr.log(
                "camera/depth_colored",
                rr.EncodedImage(contents=img_bytes, media_type="image/jpeg"),
            )


if __name__ == "__main__":
    main()
