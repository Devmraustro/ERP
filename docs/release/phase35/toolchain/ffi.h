/* Minimal libffi 3.4 x86-64 SysV declarations, written for the CPython
 * _ctypes build only. Layouts and enum values are those of libffi 3.4.x;
 * the functions are provided by the system libffi.so.8 that is present. */
#ifndef FFI_MIN_H
#define FFI_MIN_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef enum { FFI_OK = 0, FFI_BAD_TYPEDEF = 1, FFI_BAD_ABI = 2 } ffi_status;
typedef enum ffi_abi {
  FFI_FIRST_ABI = 1, FFI_WIN64 = 1, FFI_UNIX64 = 2, FFI_EFI64 = FFI_WIN64,
  FFI_GNUW64 = 4, FFI_LAST_ABI = FFI_GNUW64 + 1
} ffi_abi;
#define FFI_DEFAULT_ABI FFI_UNIX64
#define FFI_TYPE_STRUCT 13
#define FFI_TYPE_VOID 0
#define FFI_TYPE_INT 1
#define FFI_TYPE_FLOAT 2
#define FFI_TYPE_DOUBLE 3
#define FFI_TYPE_LONGDOUBLE 4
#define FFI_TYPE_UINT8 5
#define FFI_TYPE_SINT8 6
#define FFI_TYPE_UINT16 7
#define FFI_TYPE_SINT16 8
#define FFI_TYPE_UINT32 9
#define FFI_TYPE_SINT32 10
#define FFI_TYPE_UINT64 11
#define FFI_TYPE_SINT64 12
#define FFI_TYPE_STRUCT 13
#define FFI_TYPE_POINTER 14
typedef struct _ffi_type {
  size_t size;
  unsigned short alignment;
  unsigned short type;
  struct _ffi_type **elements;
} ffi_type;
typedef struct {
  void *gp_offset_storage;
} ffi_raw_unused;
typedef struct {
  unsigned long flags;
  ffi_type *rtype;
  unsigned int nargs;
  ffi_type **arg_types;
} ffi_cif;
typedef struct {
  unsigned long gp_offset;
  unsigned long fp_offset;
  void *overflow_arg_area;
  void *reg_save_area;
} ffi_gp_offset_unused;
typedef struct {
  long double ld;
  double d;
  float f;
  long l;
  int i;
  short s;
  char c;
  void *p;
} ffi_arg_storage_unused;
ffi_status ffi_prep_cif(ffi_cif *cif, ffi_abi abi, unsigned int nargs,
                        ffi_type *rtype, ffi_type **atypes);
ffi_status ffi_prep_cif_var(ffi_cif *cif, ffi_abi abi, unsigned int nfixedargs,
                            unsigned int ntotalargs, ffi_type *rtype, ffi_type **atypes);
void ffi_call(ffi_cif *cif, void (*fn)(void), void *rvalue, void **avalue);
ffi_type *ffi_get_struct_offsets(ffi_abi abi, ffi_type *struct_type, size_t *offsets);
void ffi_closure_free(void *);
void *ffi_closure_alloc(size_t size, void **code);
#ifdef __cplusplus
}
#endif
#endif
/* Primitive type descriptors exported by libffi. */
extern ffi_type ffi_type_void, ffi_type_uint8, ffi_type_sint8, ffi_type_uint16,
    ffi_type_sint16, ffi_type_uint32, ffi_type_sint32, ffi_type_uint64,
    ffi_type_sint64, ffi_type_float, ffi_type_double, ffi_type_longdouble,
    ffi_type_pointer;
/* Macros in libffi's own ffi.h; libffi exports only the fixed-width ones. */
#define ffi_type_schar ffi_type_sint8
#define ffi_type_uchar ffi_type_uint8
#define ffi_type_sshort ffi_type_sint16
#define ffi_type_ushort ffi_type_uint16
#define ffi_type_sint ffi_type_sint32
#define ffi_type_uint ffi_type_uint32
#define ffi_type_slong ffi_type_sint64
#define ffi_type_ulong ffi_type_uint64
typedef struct {
  /* libffi 3.4 x86-64 closure: trampoline code precedes this struct.
   * Only the *size* matters to CPython (it allocates sizeof(ffi_closure)). */
  ffi_cif *cif;
  void (*fun)(ffi_cif *, void *, void **, void *);
  void *user_data;
  void *codeloc;
  char tramp[64];
} ffi_closure;
typedef unsigned long ffi_arg;
typedef signed long ffi_sarg;
ffi_status ffi_prep_closure_loc(ffi_closure *, ffi_cif *,
                                void (*)(ffi_cif *, void *, void **, void *),
                                void *user_data, void *codeloc);
