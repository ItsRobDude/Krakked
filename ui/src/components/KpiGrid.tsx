export type Kpi = {
  label: string;
  value: string;
  change?: string;
  hint?: string;
};

export type KpiGridProps = {
  items: Kpi[];
};

export function KpiGrid({ items }: KpiGridProps) {
  return (
    <div className="kpi-grid" role="list" aria-label="Key performance indicators">
      {items.map((item) => (
        <div key={item.label} className="kpi-card" role="listitem">
          <p className="kpi-card__label">{item.label}</p>
          <p className="kpi-card__value">{item.value}</p>
          <div className="kpi-card__meta">
            {item.change ? <span className="kpi-card__pill">{item.change}</span> : null}
            {item.hint ? <span className="kpi-card__hint">{item.hint}</span> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

export default KpiGrid;
