package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.constants.SearchMode;
import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.param.Condition;
import com.qianjiangsoft.graphcore.param.SortingParam;
import lombok.Data;

/**
 * @author ulmarr8861
 * @date 2022/5/17
 */
@Data
public class GraphStatisticsParam {
    SearchMode mode;

    String edgeTypeName;

    Condition strengthCondition;

    Condition countCondition;

    SortingParam sorting;
}
