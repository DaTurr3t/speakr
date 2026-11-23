import { describe, it, expect } from 'vitest';
import { TRANSCRIPT_FORMATS, normalizeTranscriptFormat } from './transcript-format.js';

describe('normalizeTranscriptFormat', () => {
    it('keeps every supported format', () => {
        for (const fmt of TRANSCRIPT_FORMATS) {
            expect(normalizeTranscriptFormat(fmt)).toBe(fmt);
        }
        expect(TRANSCRIPT_FORMATS).toEqual(['txt', 'md', 'pdf']);
    });

    it('falls back to txt for anything else', () => {
        for (const value of [null, undefined, '', 'PDF', 'docx', 'pdf ', 42]) {
            expect(normalizeTranscriptFormat(value)).toBe('txt');
        }
    });
});
