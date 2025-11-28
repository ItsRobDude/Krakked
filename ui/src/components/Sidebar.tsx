export type SidebarItem = {
  label: string;
  description?: string;
  active?: boolean;
  badge?: string;
};

export type SidebarProps = {
  items: SidebarItem[];
  footer?: {
    label: string;
    value: string;
  };
};

export function Sidebar({ items, footer }: SidebarProps) {
  return (
    <div className="sidebar">
      <div className="sidebar__brand">
        <div className="sidebar__mark" aria-hidden="true" />
        <div>
          <p className="sidebar__eyebrow">krakked</p>
          <p className="sidebar__title">Control Room</p>
        </div>
      </div>

      <nav aria-label="Dashboard sections">
        <ul className="sidebar__list">
          {items.map((item) => (
            <li key={item.label} className={item.active ? 'sidebar__item sidebar__item--active' : 'sidebar__item'}>
              <div>
                <p className="sidebar__label">{item.label}</p>
                {item.description ? <p className="sidebar__description">{item.description}</p> : null}
              </div>
              {item.badge ? <span className="sidebar__badge">{item.badge}</span> : null}
            </li>
          ))}
        </ul>
      </nav>

      {footer ? (
        <div className="sidebar__footer">
          <p className="sidebar__footer-label">{footer.label}</p>
          <p className="sidebar__footer-value">{footer.value}</p>
        </div>
      ) : null}
    </div>
  );
}

export default Sidebar;
