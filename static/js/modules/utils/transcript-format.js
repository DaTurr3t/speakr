/**
 * Output formats of the transcript download.
 *
 * The download menu saves the chosen format in localStorage
 * (`transcriptDownloadFormat`). Anything unrecognised, such as a stale or
 * tampered value, falls back to 'txt', which has always been the default.
 */

export const TRANSCRIPT_FORMATS = ['txt', 'md', 'pdf'];

export function normalizeTranscriptFormat(value) {
    return TRANSCRIPT_FORMATS.includes(value) ? value : 'txt';
}
