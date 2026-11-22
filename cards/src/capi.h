#pragma once

#ifdef __cplusplus
extern "C" {
#endif

typedef struct CardscanHandle CardscanHandle;

CardscanHandle *cardscan_create(void);
void cardscan_destroy(CardscanHandle *handle);

const char *cardscan_version(void);

/* Sets an option by name. Supported keys:
 *   "ocr"            "1"/"0"   enable text recognition (default 1)
 *   "title_fallback" "1"/"0"   fall back to the card title (default 1)
 *   "min_track_hits" <int>     frames a track must survive (default 4)
 *   "ocr_stride"     <int>     run OCR every Nth video frame (default 2)
 *   "max_frames"     <int>     stop after N frames, 0 = all (default 0)
 *   "stride"         <int>     process every Nth video frame (default 1)
 *   "phash_index"    <path>    identify cards against a hash index (cardscan
 *                              --build-index); the optional ".orb" sidecar next to
 *                              it removes the need for the reference jpgs
 *   "debug_dir"      <path>    dump annotated frames and crops
 *   "overlay"        <path>    write an annotated video/overlay image
 * Returns 0 on success, -1 for an unknown key or bad value. */
int cardscan_set_option(CardscanHandle *handle, const char *key, const char *value);

/* Replaces the known set-code vocabulary used to snap OCR misreads.
 * Pass count = 0 to clear. */
int cardscan_set_set_codes(CardscanHandle *handle, const char *const *codes, int count);

/* Analyzes one image or one video and returns a malloc'ed UTF-8 JSON report.
 * The caller must release it with cardscan_free_string(). Returns NULL on error
 * (details available through cardscan_last_error). */
char *cardscan_analyze(CardscanHandle *handle, const char *path);

/* Returns the last error message for this handle (never NULL, may be empty). */
const char *cardscan_last_error(CardscanHandle *handle);

void cardscan_free_string(char *text);

#ifdef __cplusplus
}
#endif
