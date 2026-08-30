"use client";

import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  LabelList,
  BarChart,
  Bar,
  Cell,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
} from "recharts";

const tooltipStyle = {
  backgroundColor: "rgba(255,255,255,0.98)",
  border: "1px solid rgba(0,0,0,0.08)",
  borderRadius: "12px",
  boxShadow: "0 12px 34px -12px rgba(17,20,32,0.22)",
  fontSize: "12px",
  color: "#1d1d1f",
};

export type SocioPoint = {
  district: string;
  x: number;
  y: number;
  z?: number;
  income?: number;
  literacy?: number;
  unemployment?: number;
  urbanization?: number;
};

export function SocioScatter({
  points,
  xLabel = "Unemployment Rate (%)",
  yLabel = "Crime Rate (per 100k)",
}: {
  points: SocioPoint[];
  xLabel?: string;
  yLabel?: string;
}) {
  if (!points?.length)
    return <div className="flex h-full items-center justify-center text-faint">No data</div>;

  return (
    <ResponsiveContainer width="100%" height="100%">
      <ScatterChart margin={{ top: 12, right: 24, bottom: 24, left: 10 }}>
        <CartesianGrid stroke="rgba(0,0,0,0.06)" />
        <XAxis
          type="number"
          dataKey="x"
          name={xLabel}
          tick={{ fill: "#6b6b73", fontSize: 10.5 }}
          stroke="rgba(0,0,0,0.12)"
          label={{ value: xLabel, position: "insideBottom", offset: -14, fill: "#6b6b73", fontSize: 11 }}
        />
        <YAxis
          type="number"
          dataKey="y"
          name={yLabel}
          tick={{ fill: "#6b6b73", fontSize: 10.5 }}
          stroke="rgba(0,0,0,0.12)"
          width={48}
          label={{ value: yLabel, angle: -90, position: "insideLeft", fill: "#6b6b73", fontSize: 11 }}
        />
        <ZAxis type="number" dataKey="z" range={[80, 360]} />
        <Tooltip
          cursor={{ strokeDasharray: "3 3", stroke: "rgba(0,0,0,0.2)" }}
          contentStyle={tooltipStyle}
          formatter={(v: any, n: any) => [v, n]}
        />
        <Scatter data={points} fill="#b07d16" fillOpacity={0.75}>
          <LabelList dataKey="district" position="top" style={{ fill: "#6b6b73", fontSize: 9.5, fontWeight: 600 }} />
        </Scatter>
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export function SocioVulnerabilityBar({ data }: { data: { district: string; score: number; level: string }[] }) {
  if (!data?.length) return <div className="flex h-full items-center justify-center text-faint">No data</div>;

  const sorted = [...data].sort((a, b) => b.score - a.score);

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={sorted} layout="vertical" margin={{ top: 8, right: 30, left: 10, bottom: 4 }}>
        <CartesianGrid horizontal={false} stroke="rgba(0,0,0,0.05)" />
        <XAxis type="number" stroke="rgba(0,0,0,0.12)" tick={{ fill: "#6b6b73", fontSize: 10 }} axisLine={false} tickLine={false} domain={[0, 100]} />
        <YAxis dataKey="district" type="category" stroke="rgba(0,0,0,0.12)" width={120} tick={{ fill: "#1d1d1f", fontSize: 11, fontWeight: 500 }} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => [`${v}/100 Risk Score`, "Socio Risk Index"]} />
        <Bar dataKey="score" radius={[0, 6, 6, 0]}>
          {sorted.map((entry, idx) => (
            <Cell
              key={`cell-${idx}`}
              fill={entry.score > 75 ? "#dc2b25" : entry.score > 55 ? "#d97706" : entry.score > 35 ? "#2563eb" : "#10b981"}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function SocioRadarChart({ data }: { data: { subject: string; districtAvg: number; stateAvg: number }[] }) {
  if (!data?.length) return <div className="flex h-full items-center justify-center text-faint">No data</div>;

  return (
    <ResponsiveContainer width="100%" height="100%">
      <RadarChart cx="50%" cy="50%" outerRadius="75%" data={data}>
        <PolarGrid stroke="rgba(0,0,0,0.08)" />
        <PolarAngleAxis dataKey="subject" tick={{ fill: "#4b4b52", fontSize: 10.5, fontWeight: 500 }} />
        <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fill: "#97979f", fontSize: 9.5 }} stroke="rgba(0,0,0,0.1)" />
        <Radar name="District Profile" dataKey="districtAvg" stroke="#2563eb" fill="#3b82f6" fillOpacity={0.4} strokeWidth={2} />
        <Radar name="State Benchmark" dataKey="stateAvg" stroke="#dc2b25" fill="#ef4444" fillOpacity={0.2} strokeWidth={1.5} strokeDasharray="3 3" />
        <Tooltip contentStyle={tooltipStyle} />
      </RadarChart>
    </ResponsiveContainer>
  );
}
