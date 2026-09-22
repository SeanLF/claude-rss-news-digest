// One judge cell: a story, a rubric criterion (spec §7.1, numbered 1-7), pass or fail, one reason.
export type Criterion = 1 | 2 | 3 | 4 | 5 | 6 | 7;
export interface JudgeVerdict {
  story: number;
  criterion: Criterion;
  pass: boolean;
  reason: string;
}
