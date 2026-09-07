/** Local allowlist: provider metadata can never turn an icon into a remote request. */
const PROVIDER_LOGOS: Record<string, string> = Object.freeze({
  openai: '/providers/openai.svg', anthropic: '/providers/anthropic.svg',
  gemini: '/providers/gemini.svg', openrouter: '/providers/openrouter.svg',
  xai: '/providers/xai.svg', groq: '/providers/groq.svg',
  together: '/providers/together.svg', fireworks: '/providers/fireworks.svg',
  mistral: '/providers/mistral.svg', deepseek: '/providers/deepseek.svg',
  venice: '/providers/venice.svg', custom: '/providers/custom.svg',
});

export function providerLogo(id: string): string {
  return Object.hasOwn(PROVIDER_LOGOS, id) ? PROVIDER_LOGOS[id] : PROVIDER_LOGOS.custom;
}
