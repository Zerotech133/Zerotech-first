package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.param.Condition;
import com.qianjiangsoft.graphcore.param.SortingParam;
import lombok.Data;

@Data
public class ComplexSearchEdgeInterfaceParam {
    String edgeTypeName;

    Condition strengthCondition;

    Condition countCondition;

    Condition propertyCondition;

    Condition timeCondition;

    SortingParam sorting;
}
