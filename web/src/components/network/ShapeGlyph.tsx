/** The canvas shapes as inline SVG, so the legend and filters show exactly what is drawn. */
import type { Shape } from "./style";

export default function ShapeGlyph({ shape, color, size = 12, ring }: { shape: Shape; color: string; size?: number; ring?: "warrant" | "rowdy" }) {
  const c = size / 2;
  const r = size * 0.32;
  let el: React.ReactNode;
  switch (shape) {
    case "square":
      el = <rect x={c - r * 0.9} y={c - r * 0.9} width={r * 1.8} height={r * 1.8} rx={r * 0.3} fill={color} />;
      break;
    case "diamond":
      el = <polygon points={`${c},${c - r * 1.15} ${c + r * 1.05},${c} ${c},${c + r * 1.15} ${c - r * 1.05},${c}`} fill={color} />;
      break;
    case "triangle":
      el = <polygon points={`${c},${c - r * 1.2} ${c + r * 1.1},${c + r * 0.8} ${c - r * 1.1},${c + r * 0.8}`} fill={color} />;
      break;
    case "hexagon": {
      const pts = Array.from({ length: 6 }, (_, i) => {
        const a = (Math.PI / 3) * i - Math.PI / 6;
        return `${c + r * 1.05 * Math.cos(a)},${c + r * 1.05 * Math.sin(a)}`;
      }).join(" ");
      el = <polygon points={pts} fill={color} />;
      break;
    }
    default:
      el = <circle cx={c} cy={c} r={r} fill={color} />;
  }
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden className="shrink-0">
      {ring === "warrant" && <circle cx={c} cy={c} r={size * 0.45} fill="none" stroke="#dc2b25" strokeWidth={1.4} />}
      {ring === "rowdy" && <circle cx={c} cy={c} r={size * 0.45} fill="none" stroke="#b07d16" strokeWidth={1.2} strokeDasharray="2 1.5" />}
      {el}
    </svg>
  );
}
