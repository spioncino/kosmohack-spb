// Счётчик запросов одного вида: ответ применяется, только если после его запроса не начинался новый.
export interface LatestGuard {
  begin: () => number;
  isCurrent: (request: number) => boolean;
}

export function latestGuard(): LatestGuard {
  let last = 0;
  return {
    begin: () => {
      last += 1;
      return last;
    },
    isCurrent: (request) => request === last,
  };
}

// Сводка остаётся на экране во время загрузки, только если она относится к тому же расчёту.
export function keepsBriefing(shown: { assessment_id: string } | null, id: string): boolean {
  return shown !== null && shown.assessment_id === id;
}
