"""Owner credential input in memory, with existing Keychain clients preserved."""
import ctypes,getpass,os,sys
from workflow.runtime_config import value, service_origin
# No implicit remote destination; callers resolve the private setting at runtime.
TARGET = None

def keychain(token=None):
 if sys.platform!='darwin':raise RuntimeError('Configure a platform secret backend; this build supports macOS Keychain only')
 sec=ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security');cf=ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
 void=ctypes.c_void_p;uint=ctypes.c_uint32
 sec.SecKeychainFindGenericPassword.argtypes=[void,uint,ctypes.c_char_p,uint,ctypes.c_char_p,ctypes.POINTER(uint),ctypes.POINTER(void),ctypes.POINTER(void)]
 sec.SecKeychainFindGenericPassword.restype=ctypes.c_int32
 sec.SecKeychainItemFreeContent.argtypes=[void,void];cf.CFRelease.argtypes=[void]
 service=value('keychain_service', required=True).encode();account=value('keychain_account', required=True).encode();length=uint();data=void();item=void()
 status=sec.SecKeychainFindGenericPassword(None,len(service),service,len(account),account,ctypes.byref(length),ctypes.byref(data),ctypes.byref(item))
 try:
  if token is None:
   if status==-25300:raise RuntimeError('No worker credential in Keychain; provision the dedicated Sites API token first')
   if status:raise RuntimeError('Keychain read failed (OSStatus '+str(status)+')')
   return ctypes.string_at(data,length.value).decode()
  secret=token.encode()
  if status==0:
   sec.SecKeychainItemModifyAttributesAndData.argtypes=[void,void,uint,ctypes.c_char_p]
   result=sec.SecKeychainItemModifyAttributesAndData(item,None,len(secret),secret)
  elif status==-25300:
   sec.SecKeychainAddGenericPassword.argtypes=[void,uint,ctypes.c_char_p,uint,ctypes.c_char_p,uint,ctypes.c_char_p,void]
   result=sec.SecKeychainAddGenericPassword(None,len(service),service,len(account),account,len(secret),secret,None)
  else:raise RuntimeError('Keychain access failed (OSStatus '+str(status)+')')
  if result:raise RuntimeError('Keychain storage failed (OSStatus '+str(result)+')')
 finally:
  if data.value:sec.SecKeychainItemFreeContent(None,data)
  if item.value:cf.CFRelease(item)

def get_token():
 service_origin()  # Missing destination holds before accessing a credential backend.
 return os.environ.get('JOB_CRM_TOKEN') or keychain()

def read_owner_credential():
 """Read a Sites owner-tool credential without echo, argv, files or environment."""
 if sys.stdin.isatty():
  # getpass must never fall back to echoing a secret if terminal setup fails.
  import warnings
  with warnings.catch_warnings():
   warnings.simplefilter('error', getpass.GetPassWarning)
   token=getpass.getpass('CRM owner credential (input hidden): ').strip()
 else:
  token=sys.stdin.readline(8194).strip()
 if not token or len(token)>8192 or any(c.isspace() for c in token):
  raise RuntimeError('Invalid owner credential input')
 return token

if __name__=='__main__':
 token=getpass.getpass('Dedicated Sites API token (hidden): ').strip()
 if not token:raise SystemExit('No credential supplied')
 keychain(token)
 print('Credential stored in macOS Keychain; no token written to repository or output.')
