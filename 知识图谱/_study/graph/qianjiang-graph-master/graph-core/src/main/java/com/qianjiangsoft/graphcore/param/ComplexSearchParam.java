package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.constants.SearchMode;
import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/4/28 18:02
 */
@Data
public class ComplexSearchParam {

    SearchMode mode;

    ComplexSearchVertexParam vertexParam;

    ComplexSearchVertexParam vertexParam2;

    ComplexSearchEdgeParam edgeParam;

}
