import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "./api";
import DecisionForm from "./DecisionForm";
import PicketSchema from "./PicketSchema";
import {
  pct,
  RISK_COLOR,
  type Dashboard,
  type EventRow,
  type FeedbackStats,
  type Metrics,
  type ObjectCard,
  type Prediction,
  type Role,
} from "./types";

type Tab = "board" | "journal" | "analyst";
type Theme = "night" | "day";

const THEME_KEY = "ods-theme";

function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "day" || stored === "night") return stored;
  } catch {
    /* ignore */
  }
  return document.documentElement.getAttribute("data-theme") === "day" ? "day" : "night";
}

function writeTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* ignore */
  }
}

const ROLES: { id: Role; label: string }[] = [
  { id: "dispatcher", label: "Диспетчер" },
  { id: "analyst", label: "Аналитик" },
  { id: "manager", label: "Руководитель" },
];

function riskLabel(titles: Record<string, string> | undefined, code: string): string {
  return titles?.[code] ?? code;
}

function exportCsv(rows: Prediction[]) {
  const header = ["id", "object", "risk", "probability", "status", "source", "horizon"];
  const lines = [
    header.join(";"),
    ...rows.map((row) =>
      [
        row.id,
        `"${(row.object_name ?? row.object_id ?? "").toString().replaceAll('"', '""')}"`,
        row.risk_title,
        row.probability.toFixed(3),
        row.status,
        row.source,
        row.horizon_hours,
      ].join(";"),
    ),
  ];
  const blob = new Blob(["\uFEFF" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "predictions.csv";
  link.click();
  URL.revokeObjectURL(url);
}

export default function App() {
  const [role, setRole] = useState<Role>("dispatcher");
  const [tab, setTab] = useState<Tab>("board");
  const [who, setWho] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [board, setBoard] = useState<Dashboard | null>(null);
  const [journal, setJournal] = useState<Prediction[]>([]);
  const [filter, setFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("open");
  const [selected, setSelected] = useState<Prediction | null>(null);
  const [objectCard, setObjectCard] = useState<ObjectCard | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [feedback, setFeedback] = useState<FeedbackStats | null>(null);
  const [audit, setAudit] = useState<{ id: number; action: string; user_login: string; created_at: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [theme, setTheme] = useState<Theme>(readTheme);

  const loadBoard = useCallback(async () => {
    const data = await api.dashboard(role);
    setBoard(data);
  }, [role]);

  const loadJournal = useCallback(async () => {
    const query = new URLSearchParams();
    query.set("status", statusFilter);
    query.set("limit", "200");
    if (filter !== "all") query.set("risk_type", filter);
    setJournal(await api.predictions(role, `?${query.toString()}`));
  }, [role, filter, statusFilter]);

  useEffect(() => {
    setError(null);
    api
      .me(role)
      .then((me) => setWho(me.display_name))
      .catch((err: Error) => setError(err.message));
  }, [role]);

  useEffect(() => {
    setError(null);
    loadBoard().catch((err: Error) => setError(err.message));
    loadJournal().catch((err: Error) => setError(err.message));
  }, [loadBoard, loadJournal]);

  useEffect(() => {
    if (tab !== "analyst") return;
    setError(null);
    api
      .metrics(role)
      .then(setMetrics)
      .catch((err: Error) => {
        if (err instanceof ApiError && err.status === 403) setMetrics({ available: false, detail: err.message });
        else setError(err.message);
      });
    api.feedback(role).then(setFeedback).catch(() => setFeedback(null));
    if (role === "manager") {
      api.audit(role).then(setAudit).catch(() => setAudit([]));
    }
  }, [tab, role]);

  async function openCard(row: Prediction) {
    setSelected(row);
    setNotice(null);
    if (!row.object_id) {
      setObjectCard(null);
      setEvents([]);
      return;
    }
    try {
      const [card, ev] = await Promise.all([
        api.object(role, row.object_id),
        api.events(role, row.object_id, 30),
      ]);
      setObjectCard(card);
      setEvents(ev);
    } catch (err) {
      setError(err instanceof Error ? err.message : "не удалось открыть объект");
    }
  }

  function applyScenario(risk: "fire" | "flood") {
    setTab("board");
    setFilter(risk);
    setStatusFilter("open");
    const pool = (board?.top ?? journal).filter((row) => row.risk_type === risk && row.status === "open");
    const first = pool[0] ?? journal.find((row) => row.risk_type === risk);
    if (first) void openCard(first);
    else setNotice(`Нет открытых карточек «${risk === "fire" ? "пожар" : "подтопление"}».`);
  }

  const list = useMemo(() => journal, [journal]);

  return (
    <div className="app">
      <header className="top">
        <div>
          <h1>Москоллектор · ОДС</h1>
          <p>Прогноз инцидентов на 24 часа. Сервис не управляет оборудованием.</p>
        </div>
        <div className="top-actions">
          <label>
            Роль
            <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="ghost theme-toggle"
            onClick={() => {
              const next = theme === "night" ? "day" : "night";
              writeTheme(next);
              setTheme(next);
            }}
          >
            {theme === "day" ? "Ночь" : "День"}
          </button>
          <span className="who">{who}</span>
        </div>
      </header>

      <nav className="tabs">
        <button className={tab === "board" ? "on" : ""} onClick={() => setTab("board")}>
          Дашборд
        </button>
        <button className={tab === "journal" ? "on" : ""} onClick={() => setTab("journal")}>
          Журнал
        </button>
        <button className={tab === "analyst" ? "on" : ""} onClick={() => setTab("analyst")}>
          Аналитика
        </button>
        <div className="grow" />
        <button className="ghost" onClick={() => applyScenario("fire")}>
          Сценарий: пожар
        </button>
        <button className="ghost" onClick={() => applyScenario("flood")}>
          Сценарий: подтопление
        </button>
        {role !== "analyst" && (
          <button
            className="ghost"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                const data = await api.streamTick(role, filter === "all" ? "mix" : filter, 8);
                setNotice(`Пачка СМВУ: +${data.inserted} тревог, ${data.rules_written} правил, ${data.latency_ms} мс (SLO 5 мин).`);
                await loadBoard();
                await loadJournal();
                if (data.predictions[0]) void openCard(data.predictions[0]);
              } catch (err) {
                setError(err instanceof Error ? err.message : "не удалось принять пачку СМВУ");
              } finally {
                setBusy(false);
              }
            }}
          >
            Пачка СМВУ
          </button>
        )}
      </nav>

      {error && <p className="banner error">{error}</p>}
      {notice && <p className="banner ok">{notice}</p>}

      {tab !== "analyst" && board && (
        <section className="kpis">
          <article>
            <span>Открыто</span>
            <strong>{board.open_total}</strong>
          </article>
          <article>
            <span>Объектов</span>
            <strong>{board.objects_at_risk}</strong>
          </article>
          {Object.entries(board.open_by_risk).map(([code, n]) => (
            <article key={code} style={{ borderColor: RISK_COLOR[code] }}>
              <span>{riskLabel(board.risk_titles, code)}</span>
              <strong>{n}</strong>
            </article>
          ))}
        </section>
      )}

      {tab !== "analyst" && board && board.critical.length > 0 && (
        <section className="critical">
          <h2>Критичные (≥ 80%)</h2>
          <ul>
            {board.critical.slice(0, 6).map((row) => (
              <li key={row.id}>
                <button onClick={() => openCard(row)}>
                  {row.object_name} · {row.risk_title} · {pct(row.probability)}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {tab !== "analyst" && board && board.alarm_window && (
        <p className="muted">
          Окно витрины: {board.alarm_window.from?.replace("T", " ").slice(0, 16)} →{" "}
          {board.alarm_window.to?.replace("T", " ").slice(0, 16)}
          {board.season?.flood_season ? " · сезон паводков" : ""}
          {board.season?.heating_season ? " · отопительный сезон" : ""} · SLO 5 мин
        </p>
      )}

      {tab === "analyst" ? (
        <AnalystPanel
          metrics={metrics}
          feedback={feedback}
          audit={audit}
          role={role}
          busy={busy}
          onCalibrate={async () => {
            setBusy(true);
            setError(null);
            try {
              const data = await api.calibrate(role);
              const bits = data.applied.map((row) => `${row.risk}: ${row.base_threshold}→${row.threshold}`).join(", ");
              setNotice(`Пороги калиброваны. ${bits || "без изменений"}`);
              setMetrics(await api.metrics(role));
              setFeedback(await api.feedback(role).catch(() => null));
            } catch (err) {
              setError(err instanceof Error ? err.message : "не удалось калибровать");
            } finally {
              setBusy(false);
            }
          }}
        />
      ) : (
        <div className="layout">
          <section className="list-pane">
            <div className="filters">
              <select value={filter} onChange={(e) => setFilter(e.target.value)}>
                <option value="all">Все риски</option>
                <option value="fire">Пожар</option>
                <option value="flood">Подтопление</option>
                <option value="failure">Отказ</option>
                <option value="intrusion">НСД</option>
              </select>
              <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                <option value="open">Открытые</option>
                <option value="all">Все статусы</option>
                <option value="dispatch">Выезд</option>
                <option value="false_alarm">Ложное</option>
                <option value="watch">Наблюдение</option>
                <option value="maintenance">ТО</option>
              </select>
              <button className="ghost" onClick={() => exportCsv(journal)}>
                CSV
              </button>
              <button className="ghost" onClick={() => window.print()}>
                PDF / печать
              </button>
            </div>
            <ul className="pred-list">
              {list.map((row) => (
                <li key={row.id}>
                  <button
                    className={selected?.id === row.id ? "pred on" : "pred"}
                    onClick={() => openCard(row)}
                  >
                    <i style={{ background: RISK_COLOR[row.risk_type] }} />
                    <span className="pred-main">
                      <strong>{row.object_name ?? `объект ${row.object_id}`}</strong>
                      <em>
                        {row.risk_title} · {row.source} · {row.status}
                      </em>
                    </span>
                    <b>{pct(row.probability)}</b>
                  </button>
                </li>
              ))}
              {list.length === 0 && <li className="muted">Нет прогнозов по фильтру.</li>}
            </ul>
          </section>

          <section className="card-pane print-card">
            {!selected && <p className="muted">Выберите прогноз слева или сценарий сверху.</p>}
            {selected && (
              <>
                <p className="crumb">
                  {objectCard?.ancestors.map((item) => item.name).join(" / ")}
                  {objectCard ? ` / ${objectCard.name}` : selected.object_name}
                </p>
                <h2>
                  {selected.risk_title}
                  <small>
                    {pct(selected.probability)} на {selected.horizon_hours} ч
                  </small>
                </h2>
                <p className="explain">{selected.explanation}</p>
                <p className="reco">
                  <strong>Рекомендация.</strong> {selected.recommendation}
                </p>
                {objectCard && (
                  <PicketSchema
                    pickets={objectCard.pickets}
                    events={events}
                    objectRisks={objectCard.predictions.map((item) => item.risk_type)}
                  />
                )}
                <h3>Последние тревоги</h3>
                <ul className="events">
                  {events.slice(0, 8).map((event) => (
                    <li key={event.event_id}>
                      <time>{event.event_ts.replace("T", " ").slice(0, 16)}</time>
                      <span>
                        {event.picket ? `ПК${event.picket}` : "—"} · {event.sensor_type} · {event.value_cat}
                      </span>
                    </li>
                  ))}
                  {events.length === 0 && <li className="muted">В витрине нет тревог по объекту.</li>}
                </ul>
                <DecisionForm
                  role={role}
                  prediction={selected}
                  onDone={(updated, text) => {
                    setSelected(updated);
                    setNotice(text);
                    void loadBoard();
                    void loadJournal();
                  }}
                />
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function AnalystPanel({
  metrics,
  feedback,
  audit,
  role,
  busy,
  onCalibrate,
}: {
  metrics: Metrics | null;
  feedback: FeedbackStats | null;
  audit: { id: number; action: string; user_login: string; created_at: string }[];
  role: Role;
  busy: boolean;
  onCalibrate: () => void;
}) {
  return (
    <section className="analyst">
      <p className="note">
        Метки эвристические: модель предсказывает сработку подсистемы, а не подтверждённый пожар. Решения диспетчера
        копятся в журнале обратной связи.
      </p>
      {role === "dispatcher" && <p className="muted">Для метрик моделей переключитесь на аналитика.</p>}
      {metrics?.available === false && <p className="error">{metrics.detail}</p>}
      {metrics?.available && (
        <>
          <h2>LightGBM, test 2026</h2>
          <p className="muted">
            обучено {metrics.trained_at}
            {metrics.retrained_at ? ` · дообучение ${metrics.retrained_at}` : ""}
          </p>
          <table>
            <thead>
              <tr>
                <th>Риск</th>
                <th>Precision</th>
                <th>Recall</th>
                <th>PR-AUC</th>
                <th>Порог</th>
              </tr>
            </thead>
            <tbody>
              {metrics.test_lgbm?.map((row) => (
                <tr key={row.risk}>
                  <td>{row.title}</td>
                  <td>{pct(row.precision)}</td>
                  <td>{pct(row.recall)}</td>
                  <td>{row.pr_auc.toFixed(3)}</td>
                  <td>
                    {(row.calibrated_threshold ?? row.threshold)?.toFixed(3) ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {metrics.calibration && metrics.calibration.length > 0 && (
            <>
              <h2>Калибровка порогов</h2>
              <p className="muted">Ложные поднимают порог, выезды слегка опускают. Пишется в Postgres, не в metrics.json.</p>
              <table>
                <thead>
                  <tr>
                    <th>Риск</th>
                    <th>Порог</th>
                    <th>Выезды</th>
                    <th>Ложные</th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.calibration.map((row) => (
                    <tr key={row.risk}>
                      <td>{row.title}</td>
                      <td>{row.threshold.toFixed(3)}</td>
                      <td>{row.n_dispatch}</td>
                      <td>{row.n_false_alarm}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
      {role === "analyst" || role === "manager" ? (
        <p>
          <button className="ghost" disabled={busy} onClick={onCalibrate}>
            Калибровать пороги
          </button>
        </p>
      ) : (
        <p className="muted">Калибровка порогов — роль аналитика или руководителя.</p>
      )}
      {feedback && (
        <>
          <h2>Решения диспетчера</h2>
          <p>
            Открыто {feedback.open}, закрыто {feedback.closed}.{" "}
            {feedback.decisions.map((item) => `${item.title}: ${item.n}`).join(" · ") || "пока пусто"}
          </p>
        </>
      )}
      {role === "manager" && audit.length > 0 && (
        <>
          <h2>Аудит</h2>
          <ul className="events">
            {audit.map((row) => (
              <li key={row.id}>
                <time>{row.created_at?.replace("T", " ").slice(0, 16)}</time>
                <span>
                  {row.user_login} · {row.action}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
