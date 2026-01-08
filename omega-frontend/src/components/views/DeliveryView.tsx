"use client";

import { useEffect } from "react";
import Badge from "@/components/common/Badge";
import PageHeader from "@/components/layout/PageHeader";
import { useNavigation } from "@/store/navigation";
import { useProgramsStore } from "@/store/programs";

// Format date for display
const formatDate = (isoString: string): string => {
  const date = new Date(isoString);
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
};

// Group deliveries by date
const groupByDate = (items: Array<{ delivered_at: string }>) => {
  const groups: Record<string, typeof items> = {};
  const today = new Date().toDateString();
  const yesterday = new Date(Date.now() - 86400000).toDateString();

  items.forEach((item) => {
    const date = new Date(item.delivered_at);
    const dateStr = date.toDateString();

    let label: string;
    if (dateStr === today) {
      label = "Today";
    } else if (dateStr === yesterday) {
      label = "Yesterday";
    } else {
      label = date.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
    }

    if (!groups[label]) {
      groups[label] = [];
    }
    groups[label].push(item);
  });

  return groups;
};

export default function DeliveryView() {
  const { selectProgram } = useNavigation();
  const { deliveries, programs, fetchDeliveries, fetchPrograms } = useProgramsStore();

  useEffect(() => {
    fetchDeliveries(30); // Last 30 days
    fetchPrograms();
  }, [fetchDeliveries, fetchPrograms]);

  const groupedDeliveries = groupByDate(deliveries);
  const dateGroups = Object.keys(groupedDeliveries);

  return (
    <section className="delivery-view">
      <PageHeader
        title="Delivery"
        subtitle={`${deliveries.length} deliveries in the last 30 days`}
      />

      {deliveries.length === 0 ? (
        <div className="empty-state">
          <span style={{ fontSize: "48px" }}>📦</span>
          <p>No deliveries yet</p>
        </div>
      ) : (
        <div className="delivery-timeline">
          {dateGroups.map((dateLabel) => (
            <div key={dateLabel} className="delivery-group">
              <div className="delivery-date-header">{dateLabel}</div>
              <div className="delivery-items">
                {groupedDeliveries[dateLabel].map((delivery: any) => {
                  // Find program for navigation
                  const track = programs
                    .flatMap((p) => p.tracks)
                    .find((t) => t.id === delivery.track_id);
                  const programId = track?.program_id;

                  return (
                    <div
                      key={delivery.id}
                      className="delivery-row"
                      role="button"
                      tabIndex={0}
                      onClick={() => programId && selectProgram(programId)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && programId) selectProgram(programId);
                      }}
                    >
                      <div className="delivery-program">
                        <span className="program-title">{delivery.program_title}</span>
                        <span className="delivery-track">
                          {delivery.language_code?.toUpperCase()} {delivery.track_type === "dub" ? "Dub" : "Sub"}
                        </span>
                      </div>
                      <div className="delivery-destination">
                        <Badge label={delivery.destination} variant="success" />
                        {delivery.recipient && (
                          <span className="delivery-recipient">→ {delivery.recipient}</span>
                        )}
                      </div>
                      <div className="delivery-time">
                        {formatDate(delivery.delivered_at)}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
