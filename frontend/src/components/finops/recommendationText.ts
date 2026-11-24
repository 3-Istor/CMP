import { useTranslations } from "next-intl";

interface RecommendationText {
  title: string;
  justification: string;
  text_key?: string | null;
  text_params?: Record<string, string | number>;
}

/** Title and justification in the UI language, the backend's English text otherwise. */
export function useRecommendationText() {
  const t = useTranslations("finops.recommendationText");
  return (rec: RecommendationText) => {
    const key = rec.text_key;
    if (!key || !t.has(`${key}.title`)) return { title: rec.title, justification: rec.justification };
    return {
      title: t(`${key}.title`),
      justification: t(`${key}.justification`, rec.text_params ?? {}),
    };
  };
}
