package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.constants.SearchPathDirection;
import lombok.Data;

import java.util.HashMap;
import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/5/16
 */
@Data
public class GraphFindLoopParam {
    private List<String> vertexIdList;

    private List<String> edgeTypeNameList;

    private Integer maxStep;

    private SearchPathDirection direction;

    private String taskName;
}
