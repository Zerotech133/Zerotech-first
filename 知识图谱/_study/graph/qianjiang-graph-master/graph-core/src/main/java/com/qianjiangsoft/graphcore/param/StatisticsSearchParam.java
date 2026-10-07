package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.constants.SearchMode;
import com.qianjiangsoft.graphcore.model.EdgeType;
import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/4/29 14:31
 */
@Data
public class StatisticsSearchParam {

    SearchMode mode;

    EdgeType edgeType;

    Condition strengthCondition;

    Condition countCondition;

    SortingParam sorting;
}
