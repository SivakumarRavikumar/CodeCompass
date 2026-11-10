import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.use({
  renderer: {
    html: () => '',
    image: ({ href, text }) => href.startsWith('data:image/') ? `<span>[Image omitted: ${text}]</span>` : `<span>[Remote image omitted: ${text}]</span>`,
    link: ({ href, title, tokens }) => {
      const text = marked.parser(tokens)
      if (!/^https?:\/\//i.test(href)) return text
      return `<a href="${href}" title="${title ?? ''}" target="_blank" rel="noopener noreferrer">${text}</a>`
    },
  },
})

export function Markdown({ content }: { content: string }) {
  const html = DOMPurify.sanitize(marked.parse(content, { async: false }), {
    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'code', 'pre', 'ul', 'ol', 'li', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'a', 'span'],
    ALLOWED_ATTR: ['href', 'title', 'target', 'rel'],
  })
  return <div className="markdown" dangerouslySetInnerHTML={{ __html: html }} />
}
