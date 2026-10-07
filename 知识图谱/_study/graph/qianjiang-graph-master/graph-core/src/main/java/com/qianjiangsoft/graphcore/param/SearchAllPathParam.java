package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.constants.SearchPathDirection;
import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/4/28 18:02
 */
@Data
@AllArgsConstructor
@NoArgsConstructor
public class SearchAllPathParam {

    Vertex source;
    Vertex destination;
    List<EdgeType> edgeTypeList;
    Long maxStep;
    SearchPathDirection direction;
    Long totalRoad;
}
