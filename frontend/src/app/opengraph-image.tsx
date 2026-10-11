import { ImageResponse } from "next/og";

export const alt = "Unfold: turn a repository into a guide you can explain";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: 96,
          background: "#fbfaf6",
          color: "#14161c",
        }}
      >
        <div style={{ fontSize: 40, color: "#9a6519" }}>Unfold</div>
        <div style={{ fontSize: 96, marginTop: 24, lineHeight: 1.05 }}>Prepare your project.</div>
        <div style={{ fontSize: 34, marginTop: 28, color: "#5b5e68" }}>
          A visual, evidence-grounded guide to any public repository.
        </div>
      </div>
    ),
    size,
  );
}
