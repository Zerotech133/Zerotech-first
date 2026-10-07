package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.constants.SearchMode;
import lombok.Data;

/**
 * @author ulmarr8861
 * @date 2022/5/5
 */
@Data
public class GraphComplexSearch {
    //搜索类型
    private SearchMode mode;

    //实体A
    ComplexSearchTagParam vertexParam;

    //实体B
    ComplexSearchTagParam vertexParam2;

    //边
    ComplexSearchEdgeInterfaceParam edgeParam;
}
