package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.constants.SearchPathDirection;
import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.Data;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/4/28 18:02
 */
@Data
public class SearchLoopPathParam {
    //选择的点
    List<Vertex> pointList;
    //最大步数
    Long maxStep;
   //边类型
    List<EdgeType> typeList;
   //方向
    SearchPathDirection direction;
}
