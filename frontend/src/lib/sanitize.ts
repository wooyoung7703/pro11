import DOMPurify from 'dompurify'

// Sanitize potentially unsafe HTML before rendering via v-html.
// Keep profile minimal for general HTML content.
export function sanitize(html: unknown): string {
  if (typeof html !== 'string') return ''
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } })
}

export default sanitize
