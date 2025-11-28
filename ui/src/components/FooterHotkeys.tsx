export type Hotkey = {
  keys: string;
  description: string;
};

export type FooterHotkeysProps = {
  hotkeys: Hotkey[];
};

export function FooterHotkeys({ hotkeys }: FooterHotkeysProps) {
  return (
    <div className="footer-hotkeys" aria-label="Keyboard shortcuts">
      {hotkeys.map((hotkey) => (
        <div key={hotkey.keys} className="footer-hotkeys__item">
          <span className="footer-hotkeys__keys">{hotkey.keys}</span>
          <span className="footer-hotkeys__description">{hotkey.description}</span>
        </div>
      ))}
    </div>
  );
}

export default FooterHotkeys;
