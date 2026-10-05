import { Link } from "react-router-dom";

interface Props {
  id: string;
  title: string;
  isPasswordProtected?: boolean;
  distanceM?: number | null;
  status?: "active" | "expired" | "collected";
}

export default function DropSummary({ id, title, isPasswordProtected, distanceM, status }: Props) {
  return (
    <Link to={`/drop/${id}`}>
      <strong>{title || "Untitled drop"}</strong>
      {isPasswordProtected && " 🔒"}
      {status && status !== "active" && <span className="status-badge"> · {status}</span>}
      {distanceM != null && <span className="hint"> · {Math.round(distanceM)}m away</span>}
    </Link>
  );
}
