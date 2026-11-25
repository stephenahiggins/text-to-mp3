#import <React/RCTBridgeModule.h>
#import <React/RCTEventEmitter.h>
@interface RCT_EXTERN_MODULE(ReadingShareQueue, RCTEventEmitter)
RCT_EXTERN_METHOD(setActive:(BOOL)active)
RCT_EXTERN_METHOD(present)
@end
