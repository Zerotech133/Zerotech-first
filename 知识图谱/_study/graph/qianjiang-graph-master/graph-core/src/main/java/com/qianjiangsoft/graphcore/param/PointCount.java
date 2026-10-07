package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.model.MergeEdge;
import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.Data;

import java.util.HashSet;
import java.util.Set;

@Data
public class PointCount {
    private Vertex vertex=new Vertex();
    private Set<MergeEdge> edgeList=new HashSet<MergeEdge>();
    private Long count=0L;

}
