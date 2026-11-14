// The shapes of /api/community answers the pages read (docs/PLAN.md, S3b).
export interface Card {
  id: number; official: boolean; name: string; author: string; origin: string[]; roast: string;
  dose_g: number | null; water_ratio: number | null; pour_count: number | null; likes: number | null;
}
export interface Page { page: number; total_pages: number; total: number; from_xbloom_users: { recipes: Card[] } }
export type Criteria = Record<string, string[]>;
