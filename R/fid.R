### fid wrappers for R

fid_resolve <- function(fids) { 
  system2(command = "/Users/simonelsasser/GitHub/fid/bin/fid", args = c("resolve",fids),stdout = TRUE)
}
