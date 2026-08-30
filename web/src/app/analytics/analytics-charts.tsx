"use client";

import {
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  CartesianGrid,
  BarChart,
  Bar,
  Cell,
} from "recharts";

const tooltipStyle = {
  backgroundColor: "rgba(255,255,255,0.98)",
  border: "1px solid rgba(0,0,0,0.08)",
  borderRadius: "12px",
  boxShadow: "0 12px 34px -12px rgba(17,20,32,0.22)",
  fontSize: "12px",
  color: "#1d1d1f",
};

type Series = {
  history: number[];
  forecast: number[];
  lower: number[];
  upper: number[];
};

export function ForecastChart({
  labels,
  future,
  series,
}: {
  labels: string[];
  future: string[];
  series: Series;
}) {
  if (!series || !labels?.length)
    return <div className="flex h-full items-center justify-center text-faint">No forecast data</div>;

  const fmt = (d: string) => {
    const dt = new Date(d);
    return `${dt.toLocaleString("en", { month: "short" })} ${dt.getDate()}`;
  };

  const lastActual = series.history[series.history.length - 1];
  const data: any[] = labels.map((w, i) => ({
    week: fmt(w),
    actual: series.history[i],
  }));

  const bridgeIdx = data.length - 1;
  data[bridgeIdx].forecast = lastActual;
  data[bridgeIdx].range = [lastActual, lastActual];

  future.forEach((w, i) => {
    data.push({
      week: fmt(w),
      forecast: series.forecast[i],
      range: [series.lower[i], series.upper[i]],
    });
  });

  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 4 }}>
        <defs>
          <linearGradient id="actualFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#e7b43c" stopOpacity={0.32} />
            <stop offset="100%" stopColor="#e7b43c" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="rgba(0,0,0,0.06)" vertical={false} />
        <XAxis
          dataKey="week"
          stroke="#6b7280"
          fontSize={10.5}
          axisLine={false}
          tickLine={false}
          // Nine ticks regardless of chart width, so on a phone the dates ran
          // together into "Jan 5Oct 29Aug 15Jun 3..." — unreadable. Letting Recharts
          // drop ticks that would collide makes the count follow the width instead
          // of the data length.
          interval="preserveStartEnd"
          minTickGap={30}
        />
        <YAxis stroke="#6b7280" fontSize={10.5} axisLine={false} tickLine={false} width={38} />
        <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: "rgba(0,0,0,0.18)" }} />
        <Area
          dataKey="range"
          stroke="none"
          fill="#5b9bd5"
          fillOpacity={0.16}
          isAnimationActive={false}
          connectNulls
        />
        <Area
          dataKey="actual"
          stroke="#e7b43c"
          strokeWidth={2.5}
          fill="url(#actualFill)"
          dot={false}
          connectNulls
        />
        <Line
          dataKey="forecast"
          stroke="#5b9bd5"
          strokeWidth={2.5}
          strokeDasharray="5 4"
          dot={{ r: 3, fill: "#5b9bd5" }}
          connectNulls
        />
        <ReferenceLine x={data[bridgeIdx].week} stroke="rgba(0,0,0,0.25)" strokeDasharray="2 3" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function ModelMetricsChart({ data }: { data: { metric: string; score: number; color: string }[] }) {
  if (!data?.length) return <div className="flex h-full items-center justify-center text-faint">No metrics</div>;

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 12, right: 16, left: -16, bottom: 4 }}>
        <CartesianGrid stroke="rgba(0,0,0,0.05)" vertical={false} />
        <XAxis dataKey="metric" stroke="#6b7280" fontSize={11} tickLine={false} axisLine={false} />
        <YAxis domain={[0, 100]} stroke="#6b7280" fontSize={10.5} tickLine={false} axisLine={false} width={36} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => [`${v}%`, "Score"]} />
        <Bar dataKey="score" radius={[6, 6, 0, 0]}>
          {data.map((entry, index) => (
            <Cell key={`cell-${index}`} fill={entry.color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function FeatureImportanceChart({ data }: { data: { feature: string; importance: number }[] }) {
  if (!data?.length) return <div className="flex h-full items-center justify-center text-faint">No features</div>;

  const sorted = [...data].sort((a, b) => b.importance - a.importance);

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={sorted} layout="vertical" margin={{ top: 4, right: 20, left: 10, bottom: 4 }}>
        <CartesianGrid horizontal={false} stroke="rgba(0,0,0,0.05)" />
        <XAxis type="number" stroke="#6b7280" fontSize={10} axisLine={false} tickLine={false} domain={[0, 100]} />
        <YAxis dataKey="feature" type="category" stroke="#6b7280" fontSize={10.5} width={110} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => [`${v}%`, "Impact Weight"]} />
        <Bar dataKey="importance" fill="#2563eb" radius={[0, 6, 6, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
