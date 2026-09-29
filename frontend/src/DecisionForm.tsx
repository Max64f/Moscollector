import { FormEvent, useState } from "react";
import { api } from "./api";
import { DECISIONS, pct, type Prediction, type Role } from "./types";

type Props = {
  role: Role;
  prediction: Prediction;
  onDone: (updated: Prediction, notice: string) => void;
};

export default function DecisionForm({ role, prediction, onDone }: Props) {
  const [decision, setDecision] = useState("dispatch");
  const [reason, setReason] = useState("");
  const [ticket, setTicket] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const closed = prediction.status !== "open";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const updated = await api.decide(role, prediction.id, {
        decision,
        reason: reason.trim() || undefined,
        create_ticket: ticket && decision === "dispatch",
      });
      let notice = `Решение «${DECISIONS.find((item) => item.id === decision)?.label}» записано.`;
      if (updated.ticket_id) {
        try {
          const sent = await api.sendTicket(role, updated.ticket_id);
          notice += ` Заявка ${sent.external_id}.`;
        } catch {
          notice += ` Черновик заявки №${updated.ticket_id}.`;
        }
      }
      onDone(updated, notice);
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    } finally {
      setBusy(false);
    }
  }

  if (closed) {
    return (
      <p className="closed">
        Решение уже записано: <strong>{prediction.status}</strong> · {pct(prediction.probability)} на{" "}
        {prediction.horizon_hours}ч
      </p>
    );
  }

  if (role === "analyst") {
    return <p className="muted">Аналитик не закрывает карточки — переключитесь на диспетчера.</p>;
  }

  return (
    <form className="decision" onSubmit={submit}>
      <div className="decision-row">
        {DECISIONS.map((item) => (
          <label key={item.id} className={decision === item.id ? "chip on" : "chip"}>
            <input
              type="radio"
              name="decision"
              value={item.id}
              checked={decision === item.id}
              onChange={() => setDecision(item.id)}
            />
            {item.label}
          </label>
        ))}
      </div>
      <textarea
        rows={3}
        placeholder="Комментарий для журнала (дым на двух пикетах, насосы…)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <label className="check">
        <input
          type="checkbox"
          checked={ticket}
          onChange={(e) => setTicket(e.target.checked)}
          disabled={decision !== "dispatch"}
        />
        Создать черновик заявки и отправить в заглушку ОДС
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy}>
        {busy ? "Запись…" : "Зафиксировать решение"}
      </button>
    </form>
  );
}
