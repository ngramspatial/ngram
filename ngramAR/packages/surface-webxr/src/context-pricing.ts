/** Standard USD list prices, verified 2026-09-07. Never substitute another model. */
export const PRICING_SOURCE = 'https://developers.openai.com/api/docs/pricing';
const OPENAI_RATES: Record<string, [number, number, number]> = {
  'gpt-6-astra': [10, 1, 50],
  'gpt-5.6-sol': [4, 0.4, 20],
  'gpt-5.6-terra': [2, 0.2, 12],
  'gpt-5.6-luna': [0.2, 0.02, 1.2],
};

export function contextPricing(provider?: string, model?: string, tokens?: number) {
  if (provider !== 'openai' || !model || !Object.hasOwn(OPENAI_RATES, model)) return null;
  const [input, cached, output] = OPENAI_RATES[model];
  // Astra's long-context threshold is published on its model page. Avoid
  // assuming other models share it when their prompt crosses 272K.
  if (model !== 'gpt-6-astra' && tokens !== undefined && tokens > 272_000) return null;
  const longContext = model === 'gpt-6-astra' && tokens !== undefined && tokens > 272_000;
  const rates = { input: input * (longContext ? 2 : 1), cached: cached * (longContext ? 2 : 1), output: output * (longContext ? 1.5 : 1) };
  return { ...rates, longContext, estimatedInputCost: Number.isFinite(tokens) && tokens! >= 0 ? tokens! * rates.input / 1_000_000 : null };
}

export function contextFraction(tokens?: number, budget?: number): number | null {
  if (!Number.isFinite(tokens) || tokens! < 0 || !Number.isFinite(budget) || budget! <= 0) return null;
  return tokens! / budget!;
}
