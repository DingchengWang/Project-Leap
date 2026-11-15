Project Leap 1.0.0: upstream license material

UI interpreter: CPython 3.14.0; its installed LICENSE.txt is retained.
Qt for Python: PySide6 6.12.0 and shiboken6 6.12.0; installed distribution metadata is retained, with the GNU LGPL/GPL texts named by that metadata.
The pinned UI dependency list is ui-requirements-lock.txt. Other installed distribution licenses and metadata are retained under ui-wheels/.
Sparkle 2.10.0 license is retained beside this directory by the native embedding step.
Scientific dependencies and Fiji retain their original license files in the runtime seed. InstanSeg model metadata remains with the engine resources.
The fluorescence and counting engine packages are independent. Both depend on one science-runtime package, which retains the scientific dependency licenses. Each engine package includes the original Project Leap Apache-2.0 LICENSE and NOTICE; fluorescence also carries the InstanSeg model license. The separate shared cellpose-model package contains one cpsam_v2 weight file and the original cellpose 4.2.1.1 package LICENSE under ThirdPartyNotices/Cellpose/LICENSE. The build report records these input hashes, and independent bundle verification checks that the redistributed license bytes match them.
