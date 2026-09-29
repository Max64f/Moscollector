import { useMemo } from "react";
import type { EventRow, Picket } from "./types";

function picketNum(value: string): number | null {
  const match = value.match(/\d+/);
  return match ? Number(match[0]) : null;
}

type Props = {
  pickets: Picket[];
  events: EventRow[];
  objectRisks: string[];
};

export default function PicketSchema({ pickets, events, objectRisks }: Props) {
  const hot = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const event of events) {
      if (!event.picket || !event.risk) continue;
      const set = map.get(event.picket) ?? new Set<string>();
      set.add(event.risk);
      map.set(event.picket, set);
    }
    return map;
  }, [events]);

  const numbered = pickets
    .map((item) => ({ ...item, n: picketNum(item.picket) }))
    .filter((item) => item.picket !== "без пикета")
    .sort((a, b) => (a.n ?? 0) - (b.n ?? 0));
  const extra = pickets.filter((item) => item.picket === "без пикета")[0];

  const highlighted = numbered.filter((item) => hot.has(item.picket));
  const rest = numbered.filter((item) => !hot.has(item.picket));
  const shown =
    numbered.length <= 36
      ? numbered
      : [...highlighted, ...rest.slice(0, Math.max(0, 36 - highlighted.length))].sort(
          (a, b) => (a.n ?? 0) - (b.n ?? 0),
        );

  const min = numbered[0]?.n;
  const max = numbered[numbered.length - 1]?.n;

  return (
    <section className="schema">
      <header className="schema-head">
        <h3>Схема пикетов</h3>
        <p>
          Коллектор {min != null && max != null ? `ПК${min} → ПК${max}` : "без разметки пикетов"} ·{" "}
          {numbered.length} пикетов · {extra ? `${extra.n_channels} каналов вне пикетов` : "все каналы привязаны"}
        </p>
        <p className="schema-note">
          Не карта Москвы: цвет — сработки и состав датчиков на пикете. Риски объекта:{" "}
          {objectRisks.length ? objectRisks.join(", ") : "нет открытых"}
        </p>
      </header>
      <div className="picket-line" role="list">
        {shown.map((item) => {
          const risks = hot.get(item.picket);
          const cls = [
            "picket",
            risks?.has("fire") ? "is-fire" : "",
            risks?.has("flood") ? "is-flood" : "",
            risks?.has("intrusion") ? "is-guard" : "",
            risks ? "is-hot" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <div key={item.picket} className={cls} role="listitem" title={item.sensor_types.join(", ")}>
              <span>ПК{item.picket}</span>
              <small>{item.n_channels}</small>
            </div>
          );
        })}
        {numbered.length > shown.length && (
          <div className="picket is-more">ещё {numbered.length - shown.length}</div>
        )}
      </div>
    </section>
  );
}
