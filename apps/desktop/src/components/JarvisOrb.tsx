import type { CoreStatus, JarvisState } from "../core/protocol";

interface JarvisOrbProps {
  compact: boolean;
  coreStatus: CoreStatus;
  state: JarvisState;
  onOpen(): void;
}

export default function JarvisOrb({
  compact,
  coreStatus,
  state,
  onOpen,
}: JarvisOrbProps) {
  const status = coreStatus === "READY" ? state : coreStatus;
  const orb = (
    <span className="orb-assembly" data-state={status} aria-hidden="true">
      <span className="orb-ring orb-ring-outer" />
      <span className="orb-ring orb-ring-inner" />
      <span className="orb-core" />
    </span>
  );

  if (compact) {
    return (
      <button className="orb-button" type="button" aria-label="打开 JARVIS" onClick={onOpen}>
        {orb}
        <span className="orb-status">{status}</span>
      </button>
    );
  }

  return <div className="orb-inline">{orb}</div>;
}
