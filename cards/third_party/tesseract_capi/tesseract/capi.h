/*
 * Minimal, ABI-stable subset of the Tesseract 5 C API.
 *
 * Used only when the system has the libtesseract runtime but not the
 * libtesseract-dev headers. The declarations below are the public C API
 * from tesseract/capi.h (Apache-2.0); keep them byte-compatible with it.
 */
#ifndef CARDSCAN_TESSERACT_CAPI_MIN_H
#define CARDSCAN_TESSERACT_CAPI_MIN_H

#ifdef __cplusplus
extern "C" {
#endif

typedef struct TessBaseAPI TessBaseAPI;

typedef enum TessPageSegMode {
  PSM_OSD_ONLY = 0,
  PSM_AUTO_OSD = 1,
  PSM_AUTO_ONLY = 2,
  PSM_AUTO = 3,
  PSM_SINGLE_COLUMN = 4,
  PSM_SINGLE_BLOCK_VERT_TEXT = 5,
  PSM_SINGLE_BLOCK = 6,
  PSM_SINGLE_LINE = 7,
  PSM_SINGLE_WORD = 8,
  PSM_CIRCLE_WORD = 9,
  PSM_SINGLE_CHAR = 10,
  PSM_SPARSE_TEXT = 11,
  PSM_SPARSE_TEXT_OSD = 12,
  PSM_RAW_LINE = 13,
  PSM_COUNT = 14
} TessPageSegMode;

TessBaseAPI *TessBaseAPICreate(void);
int TessBaseAPIInit3(TessBaseAPI *handle, const char *datapath, const char *language);
void TessBaseAPIEnd(TessBaseAPI *handle);
void TessBaseAPIDelete(TessBaseAPI *handle);
void TessBaseAPISetImage(TessBaseAPI *handle, const unsigned char *imagedata, int width,
                         int height, int bytes_per_pixel, int bytes_per_line);
void TessBaseAPISetPageSegMode(TessBaseAPI *handle, TessPageSegMode mode);
int TessBaseAPISetVariable(TessBaseAPI *handle, const char *name, const char *value);
char *TessBaseAPIGetUTF8Text(TessBaseAPI *handle);
void TessDeleteText(char *text);
int TessBaseAPIMeanTextConf(TessBaseAPI *handle);

#ifdef __cplusplus
}
#endif

#endif
