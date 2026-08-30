"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  Cell,
  CartesianGrid,
} from "recharts";

const tooltipStyle = {
  backgroundColor: "rgba(255,255,255,0.98)",
  border: "1px solid rgba(0,0,0,0.08)",
  borderRadius: "12px",
  boxShadow: "0 12px 34px -12px rgba(17,20,32,0.22)",
  fontSize: "12px",
  color: "#1d1d1f",
};

const tick = { fill: "#6b6b73", fontSize: 11 };

export function CrimeTrendsChart({ data }: { data: any[] }) {
  if (!data || data.length === 0)
    return (
      <div className="flex h-full w-full items-center justify-center text-faint">No data</div>
    );

  const sorted = [...data].sort((a, b) => b.count - a.count);
  const max = Math.max(...sorted.map((d) => d.count));

  return (
    <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}>
      <BarChart data={sorted} layout="vertical" margin={{ top: 4, right: 30, left: 8, bottom: 4 }}>
        <defs>
          {/* Saturated gold that stays visible on white (no pale/cream end). */}
          <linearGradient id="barGold" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#9a6c12" />
            <stop offset="100%" stopColor="#e0a51f" />
          </linearGradient>
        </defs>
        <CartesianGrid horizontal={false} stroke="rgba(0,0,0,0.05)" />
        <XAxis type="number" stroke="rgba(0,0,0,0.12)" tick={tick} axisLine={false} tickLine={false} />
        <YAxis
          dataKey="crime_type"
          type="category"
          stroke="rgba(0,0,0,0.12)"
          width={150}
          tick={tick}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip cursor={{ fill: "rgba(0,0,0,0.04)" }} contentStyle={tooltipStyle} />
        <Bar dataKey="count" radius={[0, 6, 6, 0]}>
          {sorted.map((d, i) => (
            <Cell key={i} fill={d.count === max ? "#dc2b25" : "url(#barGold)"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function GangActivityChart({ data }: { data: any[] }) {
  if (!data || data.length === 0)
    return (
      <div className="flex h-full w-full items-center justify-center text-faint">No data</div>
    );

  return (
    <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}>
      <RadarChart cx="50%" cy="50%" outerRadius="78%" data={data}>
        <PolarGrid stroke="rgba(0,0,0,0.1)" />
        <PolarAngleAxis dataKey="name" tick={{ fill: "#4b4b52", fontSize: 11 }} />
        <PolarRadiusAxis angle={30} domain={[0, "auto"]} tick={{ fill: "#97979f", fontSize: 10 }} stroke="rgba(0,0,0,0.12)" />
        <Radar
          name="Activity"
          dataKey="value"
          stroke="#c0231d"
          fill="#dc2b25"
          fillOpacity={0.5}
          strokeWidth={2.5}
        />
        <Tooltip contentStyle={tooltipStyle} />
      </RadarChart>
    </ResponsiveContainer>
  );
}
