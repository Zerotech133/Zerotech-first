package com.qianjiangsoft.graphapi.aop.annotation;


import com.qianjiangsoft.graphapi.constants.AnalysisFunctionType;

import java.lang.annotation.*;

/**
 * @author 邓益聪
 * @date 2023/4/18 14:57
 */

@Target({ElementType.METHOD})
@Retention(RetentionPolicy.RUNTIME)
@Inherited
public @interface AsyncAnalysisTask {

    AnalysisFunctionType type();
}
